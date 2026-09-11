/** Local UI preview only. Live execution is unconditionally disabled. */
import {createServer} from 'node:http';
import {DatabaseSync} from 'node:sqlite';
import {readFile} from 'node:fs/promises';
import worker from '../judge-site/dist/server/index.js';
const database=new DatabaseSync(':memory:');
database.exec(await readFile(new URL('../judge-site/drizzle/0000_mysterious_sharon_ventura.sql',import.meta.url),'utf8'));
const DB={prepare(sql){const statement=database.prepare(sql);let values=[];const query={bind(...args){values=args;return query;},async first(){return statement.get(...values)??null;},async all(){return{results:statement.all(...values)};},async run(){const r=statement.run(...values);return{meta:{changes:Number(r.changes)}};}};return query;}};
const server=createServer(async(req,res)=>{
 try{
  const chunks=[];let total=0;
  for await(const chunk of req){total+=chunk.length;if(total>24576){res.writeHead(413);res.end();return;}chunks.push(chunk);}
  const method=req.method||'GET';
  const request=new Request('http://127.0.0.1:18770'+req.url,{method,headers:req.headers,...(['GET','HEAD'].includes(method)?{}:{body:Buffer.concat(chunks)})});
  const response=await worker.fetch(request,{DB,LIVE_ENABLED:'false'});
  res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));
 }catch{res.writeHead(500);res.end('Preview unavailable');}
});
server.requestTimeout=15000;
server.listen(18770,'127.0.0.1',()=>process.stdout.write('Repair Bench preview http://127.0.0.1:18770 — live execution disabled\n'));
