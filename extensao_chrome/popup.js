/**
 * Extensão Chrome «Tiao Cards → CLIPP»
 *
 * Blocos:
 *   - Ping do servidor local (127.0.0.1:8765)
 *   - Leitura do HTML da aba ativa e POST /import
 *   - Escolha manual de cliente (retirada no balcão) e produtos faltantes
 *   - Relatório de conferência (importado vs site)
 */
const PORTA = 8765;
const URL_SERVIDOR = `http://127.0.0.1:${PORTA}`;

const btn = document.getElementById("btn");
const statusEl = document.getElementById("status");
const versaoEl = document.getElementById("versao");
const clientesEl = document.getElementById("clientes");
const produtosEl = document.getElementById("produtos");

// Guarda o último payload lido da página para reenviar com o cliente escolhido
// (retirada no balcão: o pedido não traz CPF e o usuário escolhe o cliente).
let ultimoDados = null;
let idVendaAtual = null;
let faltantesPendentes = [];

const VERSAO_EXTENSAO = chrome.runtime.getManifest().version;
if (versaoEl) {
  versaoEl.textContent = `Extensão v${VERSAO_EXTENSAO} (YGO+Pokémon, faltantes)`;
}

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

function limparClientes() {
  if (clientesEl) clientesEl.innerHTML = "";
}

function limparProdutos() {
  if (produtosEl) produtosEl.innerHTML = "";
}

function rotuloItemFaltante(item) {
  const ref = item.referencia || item.referencia_site || "?";
  const nome = item.descricao ? ` — ${item.descricao}` : "";
  return `qtd=${item.quantidade} ${ref}${nome}`;
}

function mostrarSelecaoProduto(item) {
  if (!produtosEl || !item) return;
  produtosEl.innerHTML = "";

  const titulo = document.createElement("div");
  titulo.className = "titulo";
  titulo.textContent = "Escolha o produto no estoque:";
  produtosEl.appendChild(titulo);

  const subtitulo = document.createElement("div");
  subtitulo.className = "subtitulo";
  subtitulo.textContent = rotuloItemFaltante(item);
  produtosEl.appendChild(subtitulo);

  const candidatos = Array.isArray(item.candidatos) ? item.candidatos : [];
  if (!candidatos.length) {
    const vazio = document.createElement("div");
    vazio.className = "vazio";
    vazio.textContent =
      "Nenhum produto parecido no estoque. Lance manualmente no CLIPP.";
    produtosEl.appendChild(vazio);
    return;
  }

  candidatos.forEach((c) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "produto-item";

    const nome = document.createElement("span");
    nome.className = "nome";
    nome.textContent = c.prod_serv || c.referencia || `Produto ${c.id_identificador}`;
    b.appendChild(nome);

    const detalhes = [];
    if (c.referencia) detalhes.push(c.referencia);
    if (c.prc_venda != null) detalhes.push(`R$ ${Number(c.prc_venda).toFixed(2)}`);
    detalhes.push(`ID ${c.id_identificador}`);
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = detalhes.join(" · ");
    b.appendChild(sub);

    b.addEventListener("click", (ev) => {
      ev.preventDefault();
      escolherProduto(item, c.id_identificador);
    });
    produtosEl.appendChild(b);
  });

  const pular = document.createElement("button");
  pular.type = "button";
  pular.className = "btn-sec";
  pular.textContent = "Pular este item";
  pular.addEventListener("click", (ev) => {
    ev.preventDefault();
    pularProdutoAtual(item);
  });
  produtosEl.appendChild(pular);
}

function proximoFaltanteComCandidatos() {
  return faltantesPendentes.find(
    (f) => Array.isArray(f.candidatos) && f.candidatos.length
  );
}

function finalizarFaltantes(mensagemBase) {
  limparProdutos();
  const restantes = faltantesPendentes.length;
  let texto = mensagemBase || "Importação concluída.";
  if (restantes) {
    texto +=
      `\n\nAinda faltam ${restantes} item(ns) para lançar manualmente no CLIPP.`;
    const linhas = faltantesPendentes.map(
      (it, i) => `${i + 1}. ${rotuloItemFaltante(it)}`
    );
    texto += `\n\n${linhas.join("\n")}`;
  } else if (faltantesPendentes.length === 0 && idVendaAtual) {
    texto += "\n\nTodos os itens com sugestão foram gravados.";
  }
  setStatus(texto, restantes ? "aviso" : "ok");
}

function iniciarResolucaoFaltantes(json) {
  idVendaAtual = json.id_venda || null;
  faltantesPendentes = Array.isArray(json.itens_faltantes)
    ? json.itens_faltantes.map((f) => ({ ...f }))
    : [];
  limparProdutos();
  const prox = proximoFaltanteComCandidatos();
  if (prox) {
    mostrarSelecaoProduto(prox);
  }
}

async function escolherProduto(item, idIdentificador) {
  if (!idVendaAtual) {
    setStatus("Venda não identificada. Reimporte o pedido.", "erro");
    return;
  }
  bloquearBotao();
  limparProdutos();
  setStatus(`Gravando ${rotuloItemFaltante(item)}...`);
  try {
    const resp = await fetch(`${URL_SERVIDOR}/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resolver_produto: {
          id_venda: idVendaAtual,
          id_identificador: idIdentificador,
          item,
        },
      }),
    });
    let json = {};
    try {
      json = await resp.json();
    } catch {
      throw new Error("Resposta inválida do servidor.");
    }
    if (!json.ok) {
      throw new Error(json.mensagem || "Falha ao gravar item.");
    }
    faltantesPendentes = faltantesPendentes.filter(
      (f) => f.indice !== item.indice
    );
    const prox = proximoFaltanteComCandidatos();
    if (prox) {
      setStatus(json.mensagem || "Item gravado. Escolha o próximo:", "ok");
      mostrarSelecaoProduto(prox);
    } else {
      finalizarFaltantes(json.mensagem);
    }
  } catch (e) {
    setStatus(String(e.message || e), "erro");
    mostrarSelecaoProduto(item);
  } finally {
    liberarBotao();
  }
}

function pularProdutoAtual(item) {
  const idx = faltantesPendentes.findIndex((f) => f.indice === item.indice);
  if (idx >= 0) {
    const [pulado] = faltantesPendentes.splice(idx, 1);
    faltantesPendentes.push(pulado);
  }
  const prox = proximoFaltanteComCandidatos();
  if (prox && prox.indice !== item.indice) {
    mostrarSelecaoProduto(prox);
    setStatus("Item pulado. Escolha o próximo ou lance manualmente depois.", "aviso");
  } else {
    finalizarFaltantes("Itens com sugestão revisados.");
  }
}

function mostrarSelecaoCliente(candidatos, nomeSite) {
  if (!clientesEl) return;
  clientesEl.innerHTML = "";

  const titulo = document.createElement("div");
  titulo.className = "titulo";
  titulo.textContent = `Escolha o cliente${nomeSite ? ` para «${nomeSite}»` : ""}:`;
  clientesEl.appendChild(titulo);

  if (!Array.isArray(candidatos) || !candidatos.length) {
    const vazio = document.createElement("div");
    vazio.className = "vazio";
    vazio.textContent =
      "Nenhum cliente parecido encontrado. Lance/escolha o cliente manualmente no CLIPP.";
    clientesEl.appendChild(vazio);
    return;
  }

  candidatos.forEach((c) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "cliente-item";

    const nome = document.createElement("span");
    nome.className = "nome";
    nome.textContent = c.nome || `Cliente ${c.id_cliente}`;
    b.appendChild(nome);

    const detalhes = [];
    if (c.documento) detalhes.push(c.documento);
    if (c.telefone) detalhes.push(c.telefone);
    detalhes.push(`ID ${c.id_cliente}`);
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = detalhes.join(" · ");
    b.appendChild(sub);

    b.addEventListener("click", (ev) => {
      ev.preventDefault();
      escolherCliente(c.id_cliente);
    });
    clientesEl.appendChild(b);
  });
}

async function escolherCliente(idCliente) {
  if (!ultimoDados) {
    setStatus("Releia a página antes de escolher o cliente.", "erro");
    return;
  }
  bloquearBotao();
  limparClientes();
  setStatus(`Importando pedido #${ultimoDados.numero_pedido} (cliente ${idCliente})...`);
  try {
    await enviarImport(ultimoDados, idCliente);
  } catch (e) {
    setStatus(String(e.message || e), "erro");
  } finally {
    liberarBotao();
  }
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
        /#?([A-Z][A-Z0-9]{2,3}-(?:[A-Z]{2}[A-Z]{0,3}\d{2,3}|P\d{2,3}|\d{2,3})(?:-[A-Z0-9]{2,12})?)/gi;

      function normalizarCodigoSite(texto) {
        let t = String(texto || "")
          .toUpperCase()
          .replace(/^#/, "")
          .trim();
        // LOBLOB-035 → LOB-035
        t = t.replace(/^([A-Z][A-Z0-9]{2,3})\1-/, "$1-");
        // LCKCRPLCKC-EN035 → LCKC-EN035
        t = t.replace(/^([A-Z][A-Z0-9]{2,3})RP\1-/, "$1-");
        // ABYR-SEABYR-ENSE2 / L26D-ML26D-ENM31 → ABYR-ENSE2 / L26D-ENM31
        t = t.replace(
          /^([A-Z][A-Z0-9]{2,3})-(?:SE|EE|[A-Z]{1,3})\1-((?:EN|PT|FR).+)$/,
          "$1-$2"
        );
        return t;
      }

      function refValidaDeBruto(bruto) {
        const n = normalizarCodigoSite(bruto);
        refRe.lastIndex = 0;
        const m = refRe.exec(n);
        return m ? m[1].toUpperCase() : null;
      }

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
        const vistos = new Set();
        function add(ref) {
          if (!ref || vistos.has(ref)) return;
          vistos.add(ref);
          out.push(ref);
        }
        // 1) Prioriza «Código: …» (evita L26D-ML26 falso dentro de L26D-ML26D-ENM31)
        const codRe = /c[oó]digo\s*:\s*([A-Z0-9\-]+)/gi;
        let mc;
        while ((mc = codRe.exec(txt || "")) !== null) {
          const ref = refValidaDeBruto(mc[1]);
          if (ref) add(ref);
        }
        // 2) Demais ocorrências, já normalizando trechos longos com set duplicado
        const brutoRe = /#?([A-Z][A-Z0-9]{2,3}-[A-Z0-9\-]{4,40})/gi;
        let mb;
        while ((mb = brutoRe.exec(txt || "")) !== null) {
          const ref = refValidaDeBruto(mb[1]);
          if (ref) add(ref);
        }
        if (out.length) return out;
        refRe.lastIndex = 0;
        let m;
        while ((m = refRe.exec(txt || "")) !== null) {
          add(m[1].toUpperCase());
        }
        return out;
      }

      // Limita a extração ao painel deste pedido (evita cartas de outras
      // áreas da página — recomendações, histórico, etc.).
      function raizDetalhePedido() {
        let melhor = null;
        let melhorLen = Infinity;
        for (const el of document.querySelectorAll(
          "main, section, article, div, table, tbody"
        )) {
          const txt = el.innerText || "";
          if (!txt || txt.length < 80 || txt.length > 450000) continue;
          if (!/Itens do Pedido/i.test(txt)) continue;
          if (!/Valor\s+(dos\s+Itens|Total)/i.test(txt)) continue;
          if (cod && !txt.includes(String(cod))) continue;
          if (txt.length < melhorLen) {
            melhor = el;
            melhorLen = txt.length;
          }
        }
        return melhor || document.body;
      }

      function raizListaCartas() {
        const raiz = raizDetalhePedido();
        for (const el of raiz.querySelectorAll("*")) {
          const t = (el.innerText || "").trim();
          if (/^\d+\s+ITENS\s*-\s*PEDIDO/i.test(t)) {
            let c = el;
            for (let i = 0; i < 8 && c.parentElement; i++) {
              c = c.parentElement;
              if (!raiz.contains(c)) break;
              const ct = c.innerText || "";
              if (
                ct.length < 500000 &&
                /Valor\s+dos\s+Itens/i.test(ct)
              ) {
                return c;
              }
            }
            return el.parentElement || el;
          }
        }
        return raiz;
      }

      function queryEscopo(seletor, raiz) {
        return (raiz || document.body).querySelectorAll(seletor);
      }

      // ---- Idioma pela BANDEIRA da carta (fonte da verdade) ----
      // A referência «#BOSH-XX» do site às vezes não bate com o idioma real;
      // a bandeira (img .../bandeiras/pt|en|fr.svg, alt «Português/Inglês/Francês»)
      // é confiável. Ela costuma ficar fora do bloco do preço, então subimos no
      // DOM até o menor ancestral que tem a bandeira sem misturar duas cartas.
      function bandeiraImg(node) {
        if (!node || !node.querySelector) return null;
        return (
          node.querySelector('img[src*="/bandeiras/"]') ||
          node.querySelector(
            'img[alt="Português"], img[alt="Inglês"], img[alt="Francês"]'
          )
        );
      }

      function idiomaDeImg(img) {
        if (!img) return null;
        const src = (img.getAttribute && img.getAttribute("src")) || img.src || "";
        const s = `${src} ${img.alt || ""} ${img.title || ""}`;
        const sl = s.toLowerCase();
        if (sl.includes("bandeiras/pt.") || /portugu/i.test(s)) return "PT";
        if (sl.includes("bandeiras/en.") || /ingl|english/i.test(s)) return "EN";
        if (sl.includes("bandeiras/fr.") || /franc|french/i.test(s)) return "FR";
        return null;
      }

      function containerComBandeira(el) {
        let cont = el;
        for (let k = 0; k < 6; k++) {
          if (bandeiraImg(cont)) return cont;
          const p = cont.parentElement;
          if (!p) break;
          const t = p.innerText || "";
          const nUnit = (t.match(/\(\s*unid|\(\s*unit/gi) || []).length;
          const cores = new Set(
            refsNoTexto(t).map((r) => r.replace(/-(PT|EN|FR)/i, "-"))
          );
          if (nUnit > 1 || cores.size > 1) break; // subir mais misturaria cartas
          cont = p;
        }
        return cont;
      }

      function extrairSelados() {
        const out = [];
        const vistos = new Set();
        // SKU livre: YG088713, yummyjf, JUSHURGULA, etc. (qualquer token após «SKU:»)
        const SKU_VAL = "([A-Za-z0-9][A-Za-z0-9._\\-]{1,30})";
        const skuInline = new RegExp("\\bSKU\\s*[:\\-]?\\s*" + SKU_VAL, "i");
        const skuSolo = new RegExp("^" + SKU_VAL + "$", "i");

        function parseMoeda(s) {
          if (!s) return 0;
          const m = String(s).match(/[\d.,]+/);
          if (!m) return 0;
          return parseFloat(m[0].replace(/\./g, "").replace(",", ".")) || 0;
        }

        function addSelado(sku, txt) {
          sku = String(sku || "").toUpperCase();
          if (!sku || sku.length < 3 || vistos.has(sku)) return;
          // Carta avulsa (tem #SET-EN001) não é produto selado/SKU.
          if (refsNoTexto(txt).length > 0) return;
          if (/#?\s*\d{1,3}\s*\/\s*\d{1,3}\b/.test(txt)) return;
          vistos.add(sku);
          let descricao = "";
          let quantidade = 1;
          const linhas = String(txt || "")
            .split(/\n/)
            .map((l) => l.trim())
            .filter(Boolean);
          for (const l of linhas) {
            const m = l.match(/^(\d+)\s*x\s+/i);
            if (m) {
              quantidade = Math.max(parseInt(m[1], 10), 1);
              descricao = l.replace(/^\d+\s*x\s+/i, "").substring(0, 60);
              break;
            }
          }
          let preco = 0;
          let precoTotal = 0;
          const um = txt.match(/R\$\s*([\d.,]+)\s*\(?\s*unid/i);
          const sub = txt.match(/Subtotal[^R]*R\$\s*([\d.,]+)/i);
          preco = parseMoeda(um ? um[1] : "");
          precoTotal = parseMoeda(sub ? sub[1] : "");
          if (preco > 0 && precoTotal > 0) {
            const inferida = Math.round(precoTotal / preco);
            if (
              inferida > quantidade &&
              Math.abs(precoTotal - preco * inferida) < 0.05
            ) {
              quantidade = inferida;
            }
          } else if (!preco && precoTotal > 0 && quantidade > 1) {
            preco = precoTotal / quantidade;
          }
          if (!precoTotal && preco > 0) {
            precoTotal = preco * quantidade;
          }
          out.push({
            sku,
            descricao,
            quantidade,
            preco_unitario: preco,
            preco_total: precoTotal,
          });
        }

        const raiz = raizDetalhePedido();
        queryEscopo("tr, li, article, div, section, tbody > tr, table", raiz).forEach((el) => {
            const txt = (el.innerText || "").trim();
            if (!txt || txt.length > 2500) return;
            const inline = txt.match(skuInline);
            if (inline) {
              addSelado(inline[1], txt);
              return;
            }
            if (!/\bSKU\b/i.test(txt)) return;
            const linhas = txt.split(/\n/).map((l) => l.trim()).filter(Boolean);
            for (let i = 0; i < linhas.length; i++) {
              const linhaSku = linhas[i].match(
                new RegExp("^SKU\\s*[:\\-]?\\s*" + SKU_VAL + "$", "i")
              );
              if (linhaSku) {
                addSelado(linhaSku[1], txt);
                break;
              }
              if (/^SKU\s*:?\s*$/i.test(linhas[i]) && linhas[i + 1]) {
                const prox = linhas[i + 1].match(skuSolo);
                if (prox) {
                  addSelado(prox[1], txt);
                  break;
                }
              }
            }
          });
        return out;
      }

      function extrairIdiomasPorRef() {
        const map = {};
        const raiz = raizListaCartas();

        queryEscopo("tr, li, article, div, td, p", raiz).forEach((el) => {
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

      const RARIDADES = [
        "Quarter Century Secret Rare",
        "Prismatic Secret Rare",
        "Platinum Secret Rare",
        "Duel Terminal Ultra Parallel Rare",
        "Duel Terminal Parallel Rare",
        "Premium Gold Rare",
        "Collectors Rare",
        "Collector's Rare",
        "Starlight Rare",
        "Starfoil Rare",
        "Star Foil",
        "Ultimate Rare",
        "Ghost Rare",
        "Gold Secret Rare",
        "Gold Rare",
        "Parallel Rare",
        "Platinum Rare",
        "Ultra Pharaohs Rare",
        "Ultra Rare",
        "Super Rare",
        "Secret Rare",
        "Short Print",
        "Black Rare",
        "Alternate Rare",
        "Rare",
        "Common",
        "Comum",
      ];

      function escaparRe(s) {
        return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      }

      function raridadeDoBloco(txt) {
        if (!txt) return null;
        const linhas = txt
          .split(/\n/)
          .map((l) => l.replace(/^[^A-Za-z0-9]+/, "").trim())
          .filter(Boolean);
        let melhor = null;
        // 1) Linha que é exatamente uma raridade (mais confiável).
        for (const l of linhas) {
          for (const r of RARIDADES) {
            if (new RegExp("^" + escaparRe(r) + "$", "i").test(l)) {
              if (!melhor || r.length > melhor.length) melhor = r;
            }
          }
        }
        if (melhor) return melhor;
        // 2) Raridade contida no texto (token inteiro), pega a mais específica.
        for (const r of RARIDADES) {
          if (new RegExp("\\b" + escaparRe(r) + "\\b", "i").test(txt)) {
            if (!melhor || r.length > melhor.length) melhor = r;
          }
        }
        return melhor;
      }

      function montarItemCarta(txt, extra) {
        extra = extra || {};
        const refs = refsNoTexto(txt);
        if (!refs.length) return null;
        // Prefere ref com idioma EN/PT/FR real (não falso ML/SL de set duplicado)
        const ref =
          refs.find((r) => /-(?:EN|PT|FR)/i.test(r)) || refs[0];
        const primeira = (txt.split(/\n/)[0] || "").trim();
        let nome = primeira
          .replace(/^\s*\d+\s*x\s+/i, "")
          .replace(/\s*\(#.*$/, "")
          .replace(/\s*\(C[oó]digo.*$/i, "")
          .trim()
          .substring(0, 80);

        let qtd = 1;
        const mq = txt.match(/^\s*(\d+)\s*x\b/i);
        if (mq) qtd = Math.max(parseInt(mq[1], 10), 1);

        let pu = moedaAntesDe(txt, "unid") || moedaAntesDe(txt, "unit");
        let pt = moedaAntesDe(txt, "subtotal");
        if (pu > 0 && pt > 0) {
          const inferida = Math.round(pt / pu);
          if (inferida > qtd && Math.abs(pt - pu * inferida) < 0.05) qtd = inferida;
        } else if (!pu && pt > 0 && qtd > 0) {
          pu = pt / qtd;
        }
        if (!pt && pu > 0) pt = pu * qtd;

        const lang =
          extra.idioma || idiomaSiglaDoBloco(txt) || idiomaDoTexto(txt);
        const rar = extra.raridade || raridadeDoBloco(txt);
        const rep =
          extra.reprint || /\(\s*reprint\s*\)|\breprint\b/i.test(txt);

        return {
          referencia: ref,
          referencia_original: ref,
          descricao: nome,
          quantidade: qtd,
          preco_unitario: pu,
          preco_total: pt,
          idioma: lang,
          raridade: rar,
          reprint: rep,
          sku: null,
        };
      }

      function parseMoedaItem(s) {
        if (!s) return 0;
        const m = String(s).match(/[\d.,]+/);
        if (!m) return 0;
        return parseFloat(m[0].replace(/\./g, "").replace(",", ".")) || 0;
      }

      function moedaAntesDe(txt, marc) {
        const m = (txt || "").match(
          new RegExp("R\\$\\s*([\\d.,]+)\\s*\\(?\\s*" + marc, "i")
        );
        return m ? parseMoedaItem(m[1]) : 0;
      }

      // ---- Pokémon (cartas avulsas): nome + número/total + raridade ----
      const NUM_POKE_RE = /#?\s*(\d{1,3})\s*\/\s*(\d{1,3})\b/;
      const RARIDADES_PKMN = [
        "Rara Secreta",
        "Secreta Rara",
        "Ultra Rara",
        "Reverse Holo",
        "Reverse Foil",
        "Holográfica",
        "Holografica",
        "V-Astro",
        "VSTAR",
        "VMAX",
        "Incomum",
        "Promo",
        "Foil",
        "Holo",
        "Rara",
        "Comum",
        "V",
      ];

      function raridadePokemonDoBloco(txt) {
        if (!txt) return null;
        const linhas = txt
          .split(/\n/)
          .map((l) => l.replace(/^[^A-Za-z0-9]+/, "").trim())
          .filter(Boolean);
        let melhor = null;
        for (const l of linhas) {
          for (const r of RARIDADES_PKMN) {
            if (new RegExp("^" + escaparRe(r) + "$", "i").test(l)) {
              if (!melhor || r.length > melhor.length) melhor = r;
            }
          }
        }
        return melhor;
      }

      function montarItemPokemon(txt, idiomaForcado) {
        const m = (txt || "").match(NUM_POKE_RE);
        if (!m) return null;
        const numero = String(parseInt(m[1], 10));
        const total = String(parseInt(m[2], 10));
        const primeira = (txt.split(/\n/)[0] || "").trim();
        let nome = primeira
          .replace(/^\s*\d+\s*x\s+/i, "")
          .replace(/\s*\(#.*$/, "")
          .replace(/\s*#?\s*\d{1,3}\s*\/\s*\d{1,3}.*$/, "")
          .replace(/\s*\(.*$/, "")
          .trim()
          .substring(0, 80);

        let qtd = 1;
        const mq = txt.match(/^\s*(\d+)\s*x\b/i);
        if (mq) qtd = Math.max(parseInt(mq[1], 10), 1);

        let pu = moedaAntesDe(txt, "unid") || moedaAntesDe(txt, "unit");
        let pt = moedaAntesDe(txt, "subtotal");
        if (pu > 0 && pt > 0) {
          const inferida = Math.round(pt / pu);
          if (inferida > qtd && Math.abs(pt - pu * inferida) < 0.05) qtd = inferida;
        } else if (!pu && pt > 0 && qtd > 0) {
          pu = pt / qtd;
        }
        if (!pt && pu > 0) pt = pu * qtd;

        const lang =
          idiomaForcado || idiomaSiglaDoBloco(txt) || idiomaDoTexto(txt);
        const rar = raridadePokemonDoBloco(txt);

        return {
          jogo: "pokemon",
          nome,
          referencia: numero + "/" + total,
          referencia_original: numero + "/" + total,
          numero,
          total,
          descricao: nome,
          quantidade: qtd,
          preco_unitario: pu,
          preco_total: pt,
          idioma: lang,
          raridade: rar,
          reprint: false,
          sku: null,
        };
      }

      function extrairItensPokemon() {
        const raiz = raizListaCartas();
        const candidatos = [];
        queryEscopo("tr, li, article, div, section", raiz).forEach((el) => {
            const txt = (el.innerText || "").trim();
            if (!txt || txt.length > 2000) return;
            const nUnit = (txt.match(/\(\s*unid|\(\s*unit/gi) || []).length;
            if (nUnit !== 1) return;
            if (refsNoTexto(txt).length) return; // YGO já tratado em extrairItens
            const nums = txt.match(/#?\s*\d{1,3}\s*\/\s*\d{1,3}\b/g) || [];
            if (nums.length !== 1) return; // exatamente uma carta
            candidatos.push(el);
          });
        const internos = candidatos.filter(
          (el) => !candidatos.some((o) => o !== el && el.contains(o))
        );
        const out = [];
        internos.forEach((el) => {
          const cont = containerComBandeira(el);
          const contTxt = cont.innerText || "";
          const idioma =
            idiomaDeImg(bandeiraImg(cont)) ||
            idiomaSiglaDoBloco(contTxt) ||
            idiomaDoTexto(contTxt);
          const item = montarItemPokemon(el.innerText || "", idioma);
          if (item) out.push(item);
        });
        return out;
      }

      function extrairItens() {
        // Segmenta cada carta pelo elemento MAIS INTERNO do DOM que contém uma
        // única carta e exatamente um preço unitário. Assim raridade, reprint e
        // idioma ficam presos ao bloco certo (sem vazar para a carta vizinha).
        const raiz = raizListaCartas();
        const candidatos = [];
        queryEscopo("tr, li, article, div, section", raiz).forEach((el) => {
            const txt = (el.innerText || "").trim();
            if (!txt || txt.length > 2000) return;
            const nUnit = (txt.match(/\(\s*unid|\(\s*unit/gi) || []).length;
            if (nUnit !== 1) return;
            const refs = refsNoTexto(txt);
            if (!refs.length) return;
            const cores = new Set(refs.map((r) => r.replace(/-(PT|EN|FR)/i, "-")));
            if (cores.size !== 1) return;
            candidatos.push(el);
          });
        // Mantém só os elementos mais internos (descarta ancestrais).
        const internos = candidatos.filter(
          (el) => !candidatos.some((o) => o !== el && el.contains(o))
        );
        const out = [];
        internos.forEach((el) => {
          // Idioma/raridade ficam na linha da bandeira (fora do bloco do preço);
          // lê do container da carta para não pegar dados da carta vizinha.
          const cont = containerComBandeira(el);
          const contTxt = cont.innerText || "";
          const extra = {
            idioma:
              idiomaDeImg(bandeiraImg(cont)) ||
              idiomaSiglaDoBloco(contTxt) ||
              idiomaDoTexto(contTxt),
            raridade: raridadeDoBloco(contTxt),
            reprint: /\(\s*reprint\s*\)|\breprint\b/i.test(contTxt),
          };
          const item = montarItemCarta(el.innerText || "", extra);
          if (item) out.push(item);
        });
        return out;
      }

      function extrairReprintsPorRef() {
        // Marca reprint por carta: só aceita elemento que contém «(Reprint)» e
        // referências de UMA única carta (PT/EN são a mesma carta). Assim o RP
        // fica preso à carta certa e não vaza para a vizinha.
        const reprintRe = /\(\s*reprint\s*\)|\breprint\b/i;
        const map = {};
        const raiz = raizListaCartas();

        queryEscopo("tr, li, article, div, td, p", raiz).forEach((el) => {
          const txt = el.innerText || "";
          if (!txt || txt.length > 600) return;
          if (!reprintRe.test(txt)) return;
          const refs = refsNoTexto(txt);
          if (!refs.length) return;
          const cores = new Set(refs.map((r) => r.replace(/-(PT|EN|FR)/i, "-")));
          if (cores.size !== 1) return; // mais de uma carta no elemento: ignora
          refs.forEach((r) => {
            map[r] = true;
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
        reprints_por_ref: extrairReprintsPorRef(),
        selados: extrairSelados(),
        itens: [...extrairItens(), ...extrairItensPokemon()],
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
  limparProdutos();
  idVendaAtual = null;
  faltantesPendentes = [];
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
  limparClientes();
  try {
    const dados = await lerPaginaAtiva();
    if (!dados.numero_pedido) {
      throw new Error(
        "Número do pedido não encontrado.\nAbra o DETALHE do pedido (não só a lista)."
      );
    }
    ultimoDados = dados;
    await enviarImport(dados, null);
  } catch (e) {
    setStatus(String(e.message || e), "erro");
  } finally {
    liberarBotao();
  }
}

async function abrirRelatorio(relatorio) {
  if (!relatorio) return;
  const key = "relatorio_atual";
  await chrome.storage.local.set({ [key]: relatorio });
  chrome.tabs.create({
    url: chrome.runtime.getURL(`relatorio.html?k=${key}`),
  });
}

async function enviarImport(dados, clienteIdEscolhido) {
  const corpo = clienteIdEscolhido
    ? { ...dados, cliente_id_escolhido: clienteIdEscolhido }
    : dados;
  setStatus(`Enviando pedido #${dados.numero_pedido}...`);
  const resp = await fetch(`${URL_SERVIDOR}/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corpo),
  });
  let json = {};
  try {
    json = await resp.json();
  } catch {
    throw new Error("Resposta inválida do servidor.");
  }

  if (json.escolher_cliente) {
    setStatus(json.mensagem || "Escolha o cliente do pedido.", "aviso");
    mostrarSelecaoCliente(json.candidatos, json.cliente_nome);
    return;
  }

  limparClientes();

  if (json.escolher_produto && json.item_escolha) {
    iniciarResolucaoFaltantes(json);
  }

  if (json.ok) {
    const nome = json.cliente_nome ? `\nCliente: ${json.cliente_nome}` : "";
    let avisos = "";
    if (Array.isArray(json.avisos_conferencia) && json.avisos_conferencia.length) {
      avisos =
        `\n\nConferência (revise e complete manual):\n• ` +
        json.avisos_conferencia.join("\n• ");
    }
    let faltantes = "";
    if (Array.isArray(json.itens_faltantes) && json.itens_faltantes.length) {
      const linhas = json.itens_faltantes.map(
        (it, i) =>
          `${i + 1}. qtd=${it.quantidade} ${it.referencia || it.referencia_site || "?"} | R$ ${Number(it.preco_unitario).toFixed(2)} un.` +
          (it.descricao ? ` | ${it.descricao}` : "")
      );
      const sub = Number(json.subtotal_faltante || 0).toFixed(2);
      const arquivo = json.arquivo_faltantes
        ? `\n\nLista completa no Bloco de Notas:\n${json.arquivo_faltantes}`
        : "";
      faltantes =
        `\n\nSem estoque (${json.itens_faltantes.length}):\n` +
        linhas.join("\n") +
        `\nSubtotal pendente: R$ ${sub}` +
        arquivo;
    }
    let naoLidos = "";
    if (Array.isArray(json.itens_nao_lidos) && json.itens_nao_lidos.length) {
      naoLidos =
        `\n\nNão importadas: ${json.itens_nao_lidos.length} — veja a aba «Conferência».`;
    }
    if (json.relatorio) {
      await abrirRelatorio(json.relatorio);
      const pend = (json.relatorio.itens_nao_importados || []).length;
      naoLidos =
        (naoLidos || "") +
        `\n\nAba «Conferência» aberta no Chrome` +
        (pend ? ` (${pend} pendente(s)).` : ".");
    }
    if (!json.escolher_produto) {
      setStatus(`OK\n${json.mensagem || "Importado."}${nome}${avisos}${faltantes}${naoLidos}`, "ok");
    } else {
      setStatus(
        `OK\n${json.mensagem || "Importado."}${nome}${avisos}${faltantes}${naoLidos}\n\nEscolha os produtos abaixo.`,
        "ok"
      );
    }
  } else if (json.ignorado) {
    setStatus(json.mensagem || "Importação ignorada.", "aviso");
  } else {
    setStatus(json.mensagem || "Falha na importação.", "erro");
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
