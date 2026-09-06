"""Real localhost HTTP, domain and SQLite checks; synthetic business scenario only."""
import secrets
import unittest
from workbench.server import App

class PurchaseReconciliationTests(unittest.TestCase):
    def test_named_approval_receipt_and_reopened_service_reconcile(self):
        from tests.test_http_api import HTTPAPITests
        harness = HTTPAPITests()
        harness.setUp()
        self.addCleanup(harness.tearDown)
        password = secrets.token_urlsafe(32)
        def call(method, path, body=None, token=None, expected=200, key=None):
            headers = {'Authorization':'Bearer '+token} if token else {}
            if method == 'POST': headers['Idempotency-Key'] = key or secrets.token_hex(16)
            status, data, _ = harness.request(method, path, body, headers)
            self.assertEqual(expected, status, data)
            return data
        call('POST','/api/v1/setup/bootstrap', {'organization_name':'Isolated reconciliation fixture',
             'username':'maker','password':password}, expected=201)
        maker = call('POST','/api/v1/auth/login',{'username':'maker','password':password})['token']
        call('POST','/api/v1/users',{'username':'reviewer','display_name':'Fixture reviewer',
             'password':password,'roles':['admin']}, maker, expected=201)
        reviewer = call('POST','/api/v1/auth/login',{'username':'reviewer','password':password})['token']
        product = call('POST','/api/v1/products',{'sku':'L14-HTTP','name':'Fixture only',
                       'sales_price_cents':1200,'standard_cost_cents':600}, maker, expected=201)
        supplier = call('POST','/api/v1/suppliers',{'code':'HTTP-SUP','name':'Fixture supplier'}, maker, expected=201)
        purchase = call('POST','/api/v1/purchases/orders',{'supplier_id':supplier['id'],
                        'warehouse_id':'SITE-MAIN','lines':[{'product_id':product['id'],'quantity':3,'unit_price_cents':600}]},
                        maker, expected=201, key='purchase-fixture')
        endpoint = '/api/v1/purchases/orders/'+purchase['id']
        receipt_body = {'location_id':'LOC-MAIN-STOCK', 'lines':[{'purchase_line_id':purchase['lines'][0]['id'], 'accepted_quantity':3}]}
        before = harness.app.store.rows('SELECT * FROM stock_balance WHERE product_id=?',(product['id'],))
        call('POST',endpoint+'/receipts',receipt_body,maker,expected=409)
        self.assertEqual(before, harness.app.store.rows('SELECT * FROM stock_balance WHERE product_id=?',(product['id'],)))
        call('POST',endpoint+'/submit',{},maker)
        call('POST',endpoint+'/approve',{},maker,expected=409)
        self.assertEqual('pending_approval', call('GET',endpoint,token=maker)['status'])
        call('POST',endpoint+'/approve',{},reviewer)
        receipt = call('POST',endpoint+'/receipts',receipt_body,maker,expected=201)
        receipt_endpoint = '/api/v1/purchases/receipts/'+receipt['id']+'/post'
        call('POST',receipt_endpoint,{'event_key':'receipt-fixture'},reviewer,key='receipt-post')
        call('POST',receipt_endpoint,{'event_key':'receipt-fixture'},reviewer,key='receipt-post')
        reopened = App(harness.tmp.name)
        principal = reopened.api.identity.authenticate(maker)
        api_order = call('GET',endpoint,token=maker)
        service_order = reopened.api.purchasing.order(principal,purchase['id'])
        db_order = reopened.store.row('SELECT * FROM purchase_orders WHERE id=?',(purchase['id'],))
        self.assertEqual('received', api_order['status'])
        self.assertEqual(api_order['status'],service_order['status'])
        self.assertEqual(api_order['status'],db_order['status'])
        balances = call('GET','/api/v1/inventory/balances?product_id='+product['id'],token=maker)['items']
        self.assertEqual(3, sum(row['on_hand'] for row in balances))
        self.assertEqual(3, reopened.store.scalar('SELECT SUM(on_hand) FROM stock_balance WHERE product_id=?',(product['id'],)))
        self.assertEqual(1, reopened.store.scalar('SELECT COUNT(*) FROM stock_moves WHERE reference_id=?',(receipt['id'],)))
