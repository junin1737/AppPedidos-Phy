SET TERM ^ ;

CREATE OR ALTER PROCEDURE XX_INC_PDV_PEDV (
    ID_NFVENDA INTEGER
)
AS
declare variable idnfv integer;
declare variable idcliente integer;
declare variable idparcela integer;
declare variable idfmap integer;
declare variable idvendedor integer;
declare variable qtditem numeric(18,4);
declare variable idident integer;
declare variable vlr_total numeric(18,4);
declare variable vlr_desc numeric(18,4);
declare variable vlr_unit numeric(18,4);
declare variable vlr_custo numeric(18,4);
declare variable newPedido integer;
declare variable nfNumero integer;
declare variable idpedido integer;
declare variable vvfiscal numeric(18,4);
declare variable vcfiscal numeric(18,4);
declare variable chave varchar(40);
declare variable vlr_frete numeric(18,4);
declare variable obsvenda varchar(5000);
begin
  /* inc_pdv_pedv v3 */

  select nf_numero, id_vendedor, id_cliente, id_parcela, id_fmapgto, vlr_bc_frete, obs
  from tb_nfvenda_2
  where tb_nfvenda_2.id_nfvenda = :id_nfvenda
  into :nfnumero, :idvendedor, :idcliente, :idparcela, :idfmap, :vlr_frete, :obsvenda;

  insert into tb_pedido_venda (chave, id_modulo, dt_valida, id_cliente, id_vendedor, id_pedido, dt_pedido, hr_pedido, id_parcela, id_fmapgto,
  id_status, observacao) values ('', 4, current_date, :idcliente, :idvendedor, :nfNumero, current_date, current_time, :idparcela, :idfmap, 1, :obsvenda) returning id_pedido into :idpedido;

  insert into tb_ped_venda_nome( nome, cpf_cnpj, id_pedido) values ((select nome from v_clientes_2 where id_cliente = :idcliente), (select cpf from v_clientes_2 where id_cliente = :idcliente), :idpedido);

  for select i.qtd_item, i.id_identificador, i.vlr_total, i.vlr_desc, coalesce(i.vlr_unit,0.01) as vlr_unit, i.vlr_custo from tb_nfv_item_2 i
  where i.id_nfvenda = :id_nfvenda
    into :qtditem, :idident, :vlr_total, :vlr_desc, :vlr_unit, :vlr_custo
    do
    begin
      update tb_nfvenda_2 set statusdav = 'e' where id_nfvenda = :id_nfvenda;

      select prc_custo, prc_venda from v_estoque where id_identificador = :idident into vcfiscal, vvfiscal;

      vlr_unit = :vvfiscal;
      vlr_custo = :vcfiscal;

      if (:vlr_total <> (:vvfiscal * :qtditem)) then
        vlr_desc = 0;

      vlr_total = :vvfiscal * :qtditem;

      insert into tb_ped_venda_item (dt_lacto, item_cancel, id_itemped, qtd_item, vlr_total, vlr_desc, id_identificador, id_pedido, vlr_unit, prc_custo, observacao)
      values (current_date, 'N', -1, :qtditem, :vlr_total, :vlr_desc, :idident, :nfNumero, coalesce(:vlr_unit,0), :vlr_custo, :obsvenda);
    end

  update tb_pedido_venda_tot set vlr_total = :vlr_total where id_pedido = :nfNumero;

  if (:vlr_frete is null) then
    vlr_frete = 0;

  if (:vlr_frete > 0
      and not exists (select 1 from tb_ped_venda_frete where id_pedido = :idpedido)) then
  begin
    if (upper(:obsvenda) like '%MINI PAC%' or upper(:obsvenda) like '%MINIPAC%') then
      insert into tb_ped_venda_frete (id_pedido, id_fornec, vlr_frete, tipo_frete, pes_bruto, pes_liquid, qtd_volum)
      values (:idpedido, 4, :vlr_frete, '0', 0.3, 0.3, 1);
    else
      insert into tb_ped_venda_frete (id_pedido, id_fornec, vlr_frete, tipo_frete)
      values (:idpedido, 4, :vlr_frete, '0');
  end
end^

SET TERM ; ^
