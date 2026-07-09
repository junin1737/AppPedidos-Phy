function moeda(v) {
  return Number(v || 0).toLocaleString("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
}

function esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function linhasTabela(itens, mostrarMotivo) {
  if (!itens || !itens.length) {
    return '<div class="vazio">Nenhum item nesta lista.</div>';
  }
  const rows = itens
    .map((it) => {
      const ref = it.referencia || it.referencia_site || it.sku || "?";
      const motivo =
        mostrarMotivo && it.motivo
          ? `<span class="motivo">${it.motivo === "sem_estoque" ? "sem estoque" : "não lida"}</span>`
          : "";
      return `<tr>
        <td>${esc(it.quantidade)}</td>
        <td class="ref">${esc(ref)} ${motivo}</td>
        <td>${esc(it.descricao || "")}</td>
        <td>${esc(it.idioma || "—")}</td>
        <td>${esc(it.raridade || "—")}</td>
        <td>${moeda(it.preco_unitario)}</td>
        <td>${moeda(it.preco_total)}</td>
      </tr>`;
    })
    .join("");
  return `<div class="tabela-wrap"><table>
    <thead><tr>
      <th>Qtd</th><th>Referência</th><th>Descrição</th><th>Idioma</th><th>Raridade</th><th>Un.</th><th>Total</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function render(relatorio) {
  const conf = relatorio.conferencia || {};
  const pedido = relatorio.numero_pedido || "?";
  const venda = relatorio.id_venda ? ` · Venda #${relatorio.id_venda}` : "";
  const cliente = relatorio.cliente_nome || "";

  document.getElementById("titulo").textContent = `Pedido #${pedido}${venda}`;
  document.getElementById("subtitulo").textContent = cliente
    ? `Cliente: ${cliente}`
    : "Conferência importado vs site";

  const importados = relatorio.itens_importados || [];
  const pendentes = relatorio.itens_nao_importados || [];
  const qtdImp = conf.qtd_importada ?? importados.reduce((s, i) => s + Number(i.quantidade || 0), 0);
  const qtdSite = conf.qtd_site ?? 0;
  const gap = Math.max(qtdSite - qtdImp, 0);

  let avisosHtml = "";
  if (Array.isArray(conf.avisos) && conf.avisos.length) {
    avisosHtml = `<div class="avisos"><strong>Conferência do site</strong><ul>${conf.avisos
      .map((a) => `<li>${esc(a)}</li>`)
      .join("")}</ul></div>`;
  }

  document.getElementById("conteudo").innerHTML = `
    ${avisosHtml}
    <div class="cards">
      <div class="card ok">
        <div class="label">Importadas</div>
        <div class="valor">${qtdImp} carta(s)</div>
        <div>${moeda(conf.valor_importado)}</div>
      </div>
      <div class="card ${gap ? "pend" : "ok"}">
        <div class="label">Site declara</div>
        <div class="valor">${qtdSite || "—"} carta(s)</div>
        <div>${moeda(conf.valor_site)}</div>
      </div>
      <div class="card ${pendentes.length ? "pend" : "ok"}">
        <div class="label">Não importadas</div>
        <div class="valor">${pendentes.length} linha(s)</div>
        <div>${moeda(relatorio.subtotal_pendente)}</div>
      </div>
    </div>
    <div class="grid">
      <section class="importados">
        <h2>Importado no CLIPP (${importados.length} linha(s))</h2>
        ${linhasTabela(importados, false)}
      </section>
      <section class="pendentes">
        <h2>Não importado — lance manual (${pendentes.length})</h2>
        ${linhasTabela(pendentes, true)}
      </section>
    </div>`;
}

const params = new URLSearchParams(location.search);
const key = params.get("k") || "relatorio_atual";

chrome.storage.local.get(key, (data) => {
  const relatorio = data[key];
  if (!relatorio) {
    document.getElementById("erro").hidden = false;
    document.getElementById("subtitulo").textContent = "Dados não encontrados.";
    return;
  }
  render(relatorio);
});
