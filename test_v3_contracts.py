import unittest,tempfile
from pathlib import Path
from local_analyst.workspace import Workspace
from local_analyst.contracts import contract_verifier
class ContractTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.p=self.root/'fees.csv';self.p.write_text('id,amount,status,date\na,1,approved,2026-06-01\nb,1,approved,2026-06-10\nc,1,pending,2026-06-15\n');self.w=Workspace(self.root/'w');self.addCleanup(self.w.db.close);self.w.load(self.p);self.table=next(iter(self.w.frames));self.contract={'objective_id':'money','source_name':'fees.csv','operation':'sum','amount_column':'amount','output_column':'commission','unit':'USD'}
 def test_count_with_equal_numeric_value_is_not_money(self):
  self.w.query(f'SELECT COUNT(*) AS commission FROM "{self.table}"');self.assertEqual(contract_verifier([self.contract])(self.w,{},[])[0]['status'],'FAIL')
 def test_decimal_filter_contract_and_missing_context(self):
  c={**self.contract,'equals':{'status':'approved'},'date_column':'date','start':'2026-06-01','end':'2026-07-01'}
  self.w.query(f"SELECT SUM(amount) AS commission FROM \"{self.table}\" WHERE status='approved' AND date>='2026-06-01' AND date<'2026-07-01'")
  self.assertEqual(contract_verifier([c])(self.w,{},[])[0]['status'],'PASS')
  self.assertEqual(contract_verifier([{**c,'source_name':'other.csv'}])(self.w,{},[])[0]['status'],'BLOCKED')
 def test_count_currency_contract_rejected(self):
  c={**self.contract,'operation':'count'};self.assertEqual(contract_verifier([c])(self.w,{},[])[0]['status'],'BLOCKED')

 def test_wrong_filter_equal_total_is_rejected(self):
  c={**self.contract,'equals':{'status':'approved'}}
  self.w.query(f"SELECT SUM(amount) AS commission FROM \"{self.table}\" WHERE status!='pending'")
  self.assertEqual(contract_verifier([c])(self.w,{},[])[0]['status'],'FAIL')
 def test_count_plus_zero_sum_cannot_smuggle_money(self):
  self.w.query(f'SELECT COUNT(*)+SUM(amount)*0 AS commission FROM "{self.table}"')
  self.assertEqual(contract_verifier([self.contract])(self.w,{},[])[0]['status'],'FAIL')
