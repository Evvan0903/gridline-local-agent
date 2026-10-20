import fs from 'node:fs/promises';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';
const [input, output, changes='{}', renderDir=''] = process.argv.slice(2);
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(input));
const before=(await wb.inspect({kind:'table',range:'Summary!A1:B10',include:'values,formulas',tableMaxRows:10,tableMaxCols:2})).ndjson;
for(const [cell,value] of Object.entries(JSON.parse(changes))) wb.worksheets.getItem('Parameters').getRange(cell).values=[[value]];
wb.recalculate();
const after=(await wb.inspect({kind:'table',range:'Summary!A1:B10',include:'values,formulas',tableMaxRows:10,tableMaxCols:2})).ndjson;
const errors=(await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},summary:'formula error scan'})).ndjson;
if(renderDir){await fs.mkdir(renderDir,{recursive:true});
 for(const sheet of wb.worksheets.items){const blob=await wb.render({sheetName:sheet.name,range:'A1:F8',scale:1.5});await fs.writeFile(`${renderDir}/${sheet.name.replaceAll(' ','_')}.png`,new Uint8Array(await blob.arrayBuffer()));}}
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
console.log(JSON.stringify({before,after,errors}));
