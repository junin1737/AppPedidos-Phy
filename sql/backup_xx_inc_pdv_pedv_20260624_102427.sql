/* Backup XX_INC_PDV_PEDV antes da alteracao de frete */
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
begin

select nf_numero, id_vendedor, id_cliente,id_parcela,id_fmapgto from tb_nfvenda_2
where tb_nfvenda_2.id_nfvenda = :id_nfvenda
into :nfnumero, :idvendedor,:idcliente, :idparcela, :idfmap;

     insert into tb_pedido_venda (chave, id_modulo, dt_valida, id_cliente,id_vendedor, id_pedido, dt_pedido, hr_pedido, id_parcela, id_fmapgto,
     id_status) values ('', 4, current_date, :idcliente,:idvendedor,:nfNumero,current_date,current_time, :idparcela, :idfmap, 1) returning id_pedido into :idpedido;
  insert into tb_ped_venda_nome( nome, cpf_cnpj, id_pedido) values ((select nome from v_clientes_2 where id_cliente = :idcliente), (select cpf from v_clientes_2 where id_cliente = :idcliente),:idpedido);

for  select i.qtd_item, i.id_identificador, i.vlr_total, i.vlr_desc, coalesce(i.vlr_unit,0.01) as vlr_unit, i.vlr_custo from tb_nfv_item_2 i
where i.id_nfvenda = :id_nfvenda
  into :qtditem, :idident, :vlr_total, :vlr_desc, :vlr_unit, :vlr_custo
    do
    begin
    update tb_nfvenda_2 set statusdav = 'e' where id_nfvenda = :id_nfvenda;

     select prc_custo, prc_venda from v_estoque where id_identificador = :idident into vcfiscal, vvfiscal;


     vlr_unit = :vvfiscal;
     vlr_custo = :vcfiscal;

     if (:vlr_total <> (:vvfiscal * :qtditem)) then
        vlr_desc= 0;
     
     vlr_total = :vvfiscal * :qtditem;

     

     insert into tb_ped_venda_item (dt_lacto, item_cancel, id_itemped, qtd_item, vlr_total, vlr_desc, id_identificador, id_pedido, vlr_unit, prc_custo)
     values (current_date, 'N',-1, :qtditem, :vlr_total, :vlr_desc, :idident, :nfNumero, coalesce(:vlr_unit,0), :vlr_custo);
    end
update tb_pedido_venda_tot set vlr_total = :vlr_total  where id_pedido = :nfNumero;
end^

SET TERM ; ^
