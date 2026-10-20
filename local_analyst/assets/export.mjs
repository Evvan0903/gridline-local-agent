import fs from 'node:fs/promises';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const [input, output] = process.argv.slice(2);
const payload = JSON.parse(await fs.readFile(input, 'utf8'));
const wb = Workbook.create();
for (const table of payload.sheets) {
  const sheet = wb.worksheets.add(table.name);
  const values = table.rows.map(row => row.map(v => v && typeof v === 'object' && v.__date ? new Date(v.__date) : typeof v === 'string' && v.startsWith('=') ? "'"+v : v));
  if (!values.length) continue;
  sheet.getRangeByIndexes(0, 0, values.length, table.rows[0].length).values = values;
  if (table.formulas) for(const [cell, formula] of Object.entries(table.formulas)) sheet.getRange(cell).formulas = [[formula]];
  for(const index of table.date_columns || []) sheet.getRangeByIndexes(1,index,values.length-1,1).format.numberFormat = 'yyyy-mm-dd';
  sheet.getRangeByIndexes(0,0,1,table.rows[0].length).format.fill = '#243750';
  sheet.getRangeByIndexes(0,0,1,table.rows[0].length).format.font.color = '#FFFFFF';
  sheet.getRangeByIndexes(0,0,1,table.rows[0].length).format.font.bold = true;
  sheet.freezePanes.freezeRows(1);
  sheet.showGridLines = false;
  sheet.getRangeByIndexes(0,0,Math.min(values.length,1000),table.rows[0].length).format.columnWidth = 120;
}
if(payload.financial) for(const table of payload.sheets) {
 const sheet=wb.worksheets.getItem(table.name),nr=table.rows.length,nc=table.rows[0].length;
 const range=sheet.getRangeByIndexes(0,0,nr,nc);
 range.format.font.name='Arial';range.format.font.size=10;
 sheet.getRangeByIndexes(0,0,1,nc).format.wrapText=true;
 sheet.getRangeByIndexes(0,0,1,nc).format.rowHeight=36;
 for(let c=0;c<nc;c++) {
  const strings=table.rows.slice(0,20).map(r=>String(r[c]??''));
  sheet.getRangeByIndexes(0,c,nr,1).format.columnWidth=Math.min(55,Math.max(14,...strings.map(v=>v.length+2)));
  if(nr>1 && table.rows.slice(1).some(r=>typeof r[c]==='number')) {
   const values=sheet.getRangeByIndexes(1,c,nr-1,1);
   values.format.numberFormat=/^rate2?$/.test(String(table.rows[0][c]))?'0.00%':/days$|_row$|_eligible$/.test(String(table.rows[0][c]))?'0':/^multiplier|fx_units/.test(String(table.rows[0][c]))?'0.0000':'#,##0.00;(#,##0.00);0.00';
   if(table.name==='Inputs'||table.name==='Parameters')values.format.font.color='#245CC4';
  }
 }
 for(const [cell,formula] of Object.entries(table.formulas||{})) {sheet.getRange(cell).format.font.color=table.name==='Summary'||table.name==='Reconciliation'?'#17253A':formula.includes("'")?'#287044':'#17253A';sheet.getRange(cell).format.numberFormat='#,##0.00;(#,##0.00);0.00';}
 if(table.name==='Summary'&&nc>2&&nr>1){sheet.getRangeByIndexes(1,2,nr-1,1).format.wrapText=true;sheet.getRangeByIndexes(1,0,nr-1,nc).format.rowHeight=45;}
 if(table.name==='Parameters'&&nr>1){const params=sheet.getRangeByIndexes(1,1,nr-1,1);params.format.fill='#FFF2CC';params.format.numberFormat='0.0000########';}
 if(table.name==='Model Guide') {sheet.getRangeByIndexes(1,1,nr-1,1).format.wrapText=true;sheet.getRangeByIndexes(1,1,nr-1,1).format.rowHeight=75;}
}
wb.recalculate();
const result = await SpreadsheetFile.exportXlsx(wb);
await result.save(output);
console.log(JSON.stringify({output, sheets:payload.sheets.map(s=>({name:s.name,rows:s.rows.length}))}));
