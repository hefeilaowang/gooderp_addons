# -*- coding: utf-8 -*-

from odoo import models, fields, api
import hashlib,base64, httplib2
import json, urllib
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError


class express_menu_config(models.Model):
    """
    快递面单设置 目前只支持 下面这几个快递
    EMS 顺丰 圆通 百世 中通 韵达 申通 德邦 宅急送 京东 信丰 全峰 跨越速运 安能 快捷 天天 国通 优速
    """
    _name = 'express.menu.config'
    name = fields.Char(u'快递公司名称', required=True)
    abbreviation = fields.Char(u'简称', required=True)
    customername = fields.Char(u'客户名称')
    customerpwd = fields.Char(u'客户密码')
    monthcode = fields.Char(u'月结号')
    sendsite = fields.Char(u'发送站点')
    logisticcode = fields.Char(u'快递单号')

class warehouse(models.Model):
    _name = 'warehouse'
    _inherit = ['warehouse', 'state.city.county']
    detail_address = fields.Char(u'详细地址')
    principal_id = fields.Many2one('staff', u'负责人')

class wh_move(models.Model):
    """
    生成快递电子面单
    """
    _name = 'wh.move'
    _inherit = ['wh.move', 'state.city.county']
    express_menu = fields.Text(u'快递面单', copy=False)

    def get_shipping_type_config(self, menu_type):
        """
        根据传入的快递方式返回相应的参数
        """
        express_menu = self.env['express.menu.config'].search([('abbreviation', '=', menu_type)])
        if not express_menu:
            raise UserError(u"单据%s,承运商暂不支持 或者承运商商户简称输入错误(%s)"%(self.name, menu_type))
        shipping_type_config = dict(ShipperCode=menu_type,
                                    CustomerName=express_menu.customername or '',
                                    CustomerPwd=express_menu.customerpwd or '',
                                    MonthCode=express_menu.monthcode or '',
                                    SendSite=express_menu.sendsite or '',
                                    )
        return shipping_type_config

    def get_sender(self, ware_hosue_row, pakge_sequence):
        sender = dict(Company=ware_hosue_row.company_id.name,
                      Name=ware_hosue_row.principal_id.name or ware_hosue_row.company_id.name or '',
                      Mobile=ware_hosue_row.principal_id.work_phone or ware_hosue_row.company_id.phone,
                      ProvinceName=ware_hosue_row.province_id.name  or u'上海',
                      CityName=ware_hosue_row.city_id.city_name or u'上海',
                      ExpAreaName=ware_hosue_row.county_id.county_name  or u'浦东新区',
                      Address=(ware_hosue_row.detail_address or u'金海路2588号B-213') + u'\r\n格子号：' + pakge_sequence if pakge_sequence else '')
        return sender

    def get_receiver_goods_message(self):
        #TODO: 要发货的详细地址字段 (有疑问)
        ORIGIN_EXPLAIN = {
            'wh.internal': 'wh.internal',
            'wh.out.others': 'wh.out',
            'buy.receipt.return': 'buy.receipt',
            'sell.delivery.sell': 'sell.delivery',
        }
        if ORIGIN_EXPLAIN.get(self.origin):
            model_row = self.env[ORIGIN_EXPLAIN.get(self.origin)
                                ].search([('sell_move_id', '=', self.id)])
        receiver = dict(Company=' ',
                        Name=model_row.partner_id.name,
                        Mobile=model_row.address_id.mobile,
                        ProvinceName=model_row.address_id.province_id.name  or u'上海',
                        CityName=model_row.address_id.city_id.city_name or u'上海',
                        ExpAreaName=model_row.address_id.county_id.county_name or u'浦东新区',
                        Address=model_row.address_id.detail_address or u'金海路2588号B-213')

        goods = []
        qty = 0
        for line in self.line_out_ids:
            goods.append(dict(GoodsName=line.goods_id.name, #产品名称
                              Goodsquantity=int(line.goods_qty), #产品数量
                              GoodsWeight=1.0, #产品重量
                              GoodsCode=line.goods_id.code or '', # 产品编码
                              GoodsPrice=0.0, #产品价格
                             ))
            qty += 1
        return receiver, goods, qty

    @api.model
    def get_express_menu(self):
        expressconfigparam = self.env['ir.config_parameter']
        appid = expressconfigparam.get_param('express_menu_app_id', default='')
        appkey = expressconfigparam.get_param('express_menu_app_key', default='')
        path = expressconfigparam.get_param('express_menu_oder_url', default='')
        header = safe_eval(expressconfigparam.get_param('express_menu_request_headers',
                                                        default=''))
        order_code = self.name
        sender = self.get_sender(self.warehouse_id, self.pakge_sequence)
        remark = self.note or '小心轻放'
        shipping_type = self.express_type or 'YTO'
        receiver, commodity, qty = self.get_receiver_goods_message()
        request_data = dict(OrderCode=order_code, PayType=1, ExpType=1, Cost=1.0, OtherCost=1.0,
                            Sender=sender, Receiver=receiver, Commodity=commodity, Weight=1.0,
                            Quantity=qty, Volume=0.0, Remark=remark, IsReturnPrintTemplate=1)
        request_data.update(self.get_shipping_type_config(shipping_type))
        request_data = json.dumps(request_data)
        data = {'RequestData': request_data,
                'EBusinessID': appid,
                'RequestType': '1007',
                'DataType': '2',
                'DataSign': self.encrypt_kdn(request_data, appkey)}
        http = httplib2.Http()
        response, content = http.request(path, 'POST', headers=header, body=urllib.urlencode(data))
        content = content.replace('true', 'True').replace('false', 'False')
        self.express_code = (safe_eval(content).get('Order', {})).get('LogisticCode', "")
        self.express_menu = str(safe_eval(content).get('PrintTemplate'))
        if not self.express_code:
            raise UserError("获取快递面单失败!\n原因:%s"%str(content))
        return str(safe_eval(content).get('PrintTemplate'))

    def encrypt_kdn(self, data, appkey):
        """
        数据加密
        """
        key = base64.b64encode(hashlib.md5("%s%s" % (data, appkey)).hexdigest(), altchars=None)
        return urllib.quote(key, safe='/')

    @api.model
    def get_package_list_data(self, move_row):
        # 装箱单明细行数据
        line_dict = []
        total_price = 0
        for line in move_row.line_out_ids:
            # code, name, goods_qty, price, subtotal
            line_dict.append([line.goods_id.code,line.goods_id.name,line.goods_qty,line.price,line.subtotal])
            total_price += line.subtotal

        # 装箱单数据
        package_list = {'0 任务栏号': [],
                     '1 内部订单号': [move_row.name],
                     '2 店铺名称': [],
                     '3 外部订单号': [move_row.name],
                     '4 收货人': [move_row.partner_id.name],
                     '5 订单日期': [move_row.date],
                     '6 应付款': [move_row.partner_id.receivable],
                     '7 lines': line_dict,
                     '8 总件数': [move_row.total_qty],
                     '9 总价格': [total_price],
                     }
        return package_list

    @api.model
    def get_moves_html(self, move_ids):
        ''' 打印快递面单+装箱单 '''
        move_rows = self.browse(move_ids)
        return_html_list = []
        for move_row in move_rows:
            if move_row.express_code:
                return_html_list.append(move_row.express_menu)
            else:
                return_html_list.append(move_row.get_express_menu())

            # 添加装箱单数据
            return_html_list.append(self.get_package_list_data(move_row))

        return return_html_list

    @api.model
    def get_moves_html_package(self, move_ids):
        ''' 打印装箱单 '''
        move_rows = self.browse(move_ids)
        return_html_list = []
        for move_row in move_rows:
            return_html_list.append(self.get_package_list_data(move_row))

        return return_html_list
        