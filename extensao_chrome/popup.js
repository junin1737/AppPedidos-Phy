const PORTA = 8765;
const URL_SERVIDOR = `http://127.0.0.1:${PORTA}`;

const btn = document.getElementById("btn");
const statusEl = document.getElementById("status");

function setStatus(texto, classe = "") {
  statusEl.textContent = texto;
  statusEl.className = classe;
}

function liberarBotao() {
  btn.disabled = false;
  btn.style.pointerEvents = "auto";
}

function bloquearBotao() {
  btn.disabled = true;
  btn.style.pointerEvents = "none";
}

async function lerPaginaAtiva() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    throw new Error("Nenhuma guia ativa.");
  }
  let host = "";
  try {
    host = new URL(tab.url || "").hostname;
  } catch {
    throw new Error("URL da guia inválida.");
  }
  if (!/tiaocards|ligamagic/i.test(host)) {
    throw new Error(
      "Abra o pedido no site tiaocards.com.br (guia do painel Admin)."
    );
  }
  const injetados = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const url = new URL(location.href);
      let cod = url.searchParams.get("cod");
      if (!cod) {
        const m = document.body.innerText.match(/#(\d{7,9})/);
        cod = m ? m[1] : null;
      }

      const refRe =
        /#?([A-Z][A-Z0-9]{2,3}-(?:[A-Z]{2}\d{2,3}|P\d{2,3}|\d{2,3})(?:-[A-Z0-9]{2,12})?)/gi;

      function idiomaSiglaDoBloco(txt) {
        const linhas = (txt || "")
          .split(/\n/)
          .map((l) => l.trim())
          .filter(Boolean);
        for (let i = 0; i < linhas.length; i++) {
          const m =
            linhas[i].match(/^(PT|EN|FR)$/i) ||
            linhas[i].match(/^(PT|EN|FR)\s+(NM|LP|MP|HP|DMG|SP|PL|GD|EX|VG|FN|M|P|NR)\b/i);
          if (m) return m[1].toUpperCase();
        }
        return null;
      }

      function idiomaDoTexto(txt) {
        if (/\[PT\]/i.test(txt)) return "PT";
        if (/\[EN\]/i.test(txt)) return "EN";
        if (/\[FR\]/i.test(txt)) return "FR";
        const limpo = txt.replace(refRe, " ");
        if (/\bPT\b|portugu/i.test(limpo)) return "PT";
        if (/\bFR\b|franc/i.test(limpo)) return "FR";
        if (/\bEN\b|english|ingl/i.test(limpo)) return "EN";
        return null;
      }

      function refsNoTexto(txt) {
        const out = [];
        refRe.lastIndex = 0;
        let m;
        while ((m = refRe.exec(txt)) !== null) {
          out.push(m[1].toUpperCase());
        }
        return out;
      }

      function extrairIdiomasPorRef() {
        const map = {};

        document.querySelectorAll("tr, li, article, div, td, p").forEach((el) => {
          const txt = el.innerText || "";
          if (!txt || txt.length > 900) return;
          const refs = refsNoTexto(txt);
          if (!refs.length) return;
          const lang = idiomaSiglaDoBloco(txt) || idiomaDoTexto(txt);
          if (!lang) return;
          refs.forEach((ref) => {
            if (!map[ref]) map[ref] = lang;
          });
        });

        return map;
      }

      return {
        numero_pedido: cod,
        html: document.documentElement.outerHTML,
        texto: document.body.innerText || "",
        url: location.href,
        idiomas_por_ref: extrairIdiomasPorRef(),
      };
    },
  });
  const result = injetados?.[0]?.result;
  if (!result?.html) {
    throw new Error("Não foi possível ler a página. Recarregue o pedido e tente de novo.");
  }
  return result;
}

async function pingServidor() {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 4000);
  try {
    const r = await fetch(`${URL_SERVIDOR}/ping`, {
      method: "GET",
      signal: ctrl.signal,
    });
    if (!r.ok) {
      throw new Error("Servidor respondeu com erro.");
    }
    return r.json();
  } finally {
    clearTimeout(t);
  }
}

async function importar() {
  bloquearBotao();
  setStatus("Verificando servidor local...");
  try {
    await pingServidor();
  } catch {
    setStatus(
      "Servidor não está rodando.\n\nAbra o app «AppPedidos CLIPP»\n(atalho na área de trabalho ou menu Iniciar).\n\nA janela pode estar na bandeja (perto do relógio).",
      "erro"
    );
    return;
  }

  setStatus("Lendo pedido na guia atual...");
  try {
    const dados = await lerPaginaAtiva();
    if (!dados.numero_pedido) {
      throw new Error(
        "Número do pedido não encontrado.\nAbra o DETALHE do pedido (não só a lista)."
      );
    }
    setStatus(`Enviando pedido #${dados.numero_pedido}...`);
    const resp = await fetch(`${URL_SERVIDOR}/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dados),
    });
    let json = {};
    try {
      json = await resp.json();
    } catch {
      throw new Error("Resposta inválida do servidor.");
    }
    if (json.ok) {
      const nome = json.cliente_nome ? `\nCliente: ${json.cliente_nome}` : "";
      let faltantes = "";
      if (Array.isArray(json.itens_faltantes) && json.itens_faltantes.length) {
        const linhas = json.itens_faltantes.map(
          (it, i) =>
            `${i + 1}. qtd=${it.quantidade} ${it.referencia} | R$ ${Number(it.preco_unitario).toFixed(2)} un.` +
            (it.descricao ? ` | ${it.descricao}` : "")
        );
        const sub = Number(json.subtotal_faltante || 0).toFixed(2);
        faltantes =
          `\n\nLance manualmente (${json.itens_faltantes.length}):\n` +
          linhas.join("\n") +
          `\nSubtotal pendente: R$ ${sub}`;
      }
      setStatus(`OK\n${json.mensagem || "Importado."}${nome}${faltantes}`, "ok");
    } else if (json.ignorado) {
      setStatus(json.mensagem || "Importação ignorada.", "aviso");
    } else {
      setStatus(json.mensagem || "Falha na importação.", "erro");
    }
  } catch (e) {
    setStatus(String(e.message || e), "erro");
  } finally {
    liberarBotao();
  }
}

btn.addEventListener("click", (ev) => {
  ev.preventDefault();
  importar();
});

liberarBotao();
setStatus("Carregando...");

pingServidor()
  .then(() => setStatus("Servidor OK — clique em Importar esta página."))
  .catch(() =>
    setStatus(
      "Servidor parado.\nAbra «AppPedidos CLIPP» no Windows.\n(o botão ainda pode ser clicado).",
      "erro"
    )
  );
