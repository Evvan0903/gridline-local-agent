import fs from 'node:fs/promises';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const [input,output,renderDir]=process.argv.slice(2);
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(input));
for(const sheet of wb.worksheets.items) {
 const values=sheet.getUsedRange().values;
 if(!values.length)continue;
 const columns=values[0].length;
 for(let c=0;c<columns;c++) {
  const strings=values.slice(0,20).map(r=>String(r[c]??''));
  const cells=sheet.getRangeByIndexes(0,c,values.length,1);
  cells.format.columnWidth=Math.min(55,Math.max(14,...strings.map(v=>v.length+2)));
  const label=String(values[0][c]??'');
  if(values.length>1&&values.slice(1).some(r=>typeof r[c]==='number')) {
   const body=sheet.getRangeByIndexes(1,c,values.length-1,1);
   body.format.numberFormat=/^rate2?$/.test(label)?'0.00%':/days$|_row$|_eligible$/.test(label)?'0':/^multiplier|fx_units/.test(label)?'0.0000':'#,##0.00;(#,##0.00);0.00';
  }
 }
 if(['Assumptions','Escalations','Exception impacts'].includes(sheet.name)) {
  sheet.getRangeByIndexes(1,0,values.length-1,columns).format.wrapText=true;
  sheet.getRangeByIndexes(1,0,values.length-1,columns).format.rowHeight=90;
 }
 if(sheet.name==='Parameters'&&values.length>1)sheet.getRangeByIndexes(1,1,values.length-1,1).format.numberFormat='0.0000########';
 if(sheet.name==='Build')for(let c=0;c<columns;c++)if(/baseline_|scenario_|delta_/.test(String(values[0][c])))sheet.getRangeByIndexes(1,c,values.length-1,1).format.numberFormat='#,##0.00;(#,##0.00);0.00';
}
wb.recalculate();
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
if(renderDir){await fs.mkdir(renderDir,{recursive:true});for(const sheet of wb.worksheets.items){const blob=await wb.render({sheetName:sheet.name,range:'A1:F8',scale:1.5});await fs.writeFile(`${renderDir}/${sheet.name.replaceAll(' ','_')}.png`,new Uint8Array(await blob.arrayBuffer()));}}
console.log('Financial formatting exported after recalculation');
