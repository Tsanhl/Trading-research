/* Futures calendar aggregation and costs; direction still comes from HR.plans. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.FuturesTools=factory();})(globalThis,function(){
'use strict';
const rootOf=s=>s.future?.root||String(s.symbol).match(/^(MES|MNQ|ES|NQ)(?:[HMUZ]\d{1,4})?$/)?.[1];
function completed(s,target){
 const base=s.barMinutes,at=Date.parse(s.asOf),sessions=s.futureSessions||[],out=[];let omitted=0;
 if(target<base||target%base!==0)return {bars:[],dropped:s.bars.length,reason:'Cannot derive a finer futures interval.'};
 for(const w of sessions){
  if(!w.known||!w.segments.length)continue;
  if(base>=1440){for(const b of s.bars)if(b.finalized!==false&&b.t===w.open&&w.close<=at&&target===base&&w.completeSessionKnown)out.push({...b,session:w.date,closeAt:w.close,durationMinutes:(w.close-w.open)/60000});continue;}
  const groups=new Map();
  for(const b of s.bars){if(b.finalized===false)continue;const end=b.t+(b.durationMinutes||base)*60000,segment=w.segments.find(([a,z])=>b.t>=a&&end<=z);if(!segment||end>at)continue;
   const start=target>=1440?w.open:segment[0]+Math.floor((b.t-segment[0])/(target*60000))*target*60000;
   if(!groups.has(start))groups.set(start,[]);groups.get(start).push(b);
  }
  for(const [start,rows] of groups){const segment=w.segments.find(([a,z])=>start>=a&&start<z),end=target>=1440?w.close:Math.min(start+target*60000,segment[1]);
   const expected=[];for(const [a,z] of w.segments)for(let t=Math.max(a,start);t<Math.min(z,end);t+=base*60000)expected.push(t);
   if(end>at||rows.length!==expected.length||rows.some((b,i)=>b.t!==expected[i]||(b.durationMinutes||base)!==base)||(target>=1440&&!w.completeSessionKnown)){omitted++;continue;}
   out.push({t:start,o:rows[0].o,h:Math.max(...rows.map(b=>b.h)),l:Math.min(...rows.map(b=>b.l)),c:rows.at(-1).c,v:rows.reduce((n,b)=>n+b.v,0),session:w.date,closeAt:end,durationMinutes:(end-start)/60000,partialPeriod:target<1440&&end-start<target*60000});
  }
 }
 out.sort((a,b)=>a.t-b.t);return {bars:out,dropped:omitted,reason:'CME calendar-aligned completed observations; holiday windows remain unknown. Breaks excluded; short final buckets labelled.'};
}
function costs(s,{feePerSide=null,slippageTicks=1}={}){const f=s.future;if(!f)return null;const fee=feePerSide??(['MES','MNQ'].includes(f.root)?1.25:2.5);if(![fee,slippageTicks].every(Number.isFinite)||fee<0||slippageTicks<0)throw Error('Invalid futures cost assumptions');const usd=2*fee+2*slippageTicks*f.tickValue;return {feePerSide:fee,slippageTicks,roundTripUsd:usd,points:usd/f.pointValue,pointValue:f.pointValue,tickSize:f.tickSize,assumed:true};}
function status(s,now=Date.now()){
 const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'America/Chicago',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',hourCycle:'h23'}).formatToParts(new Date(now)).map(x=>[x.type,x.value]));
 const d=new Date(`${parts.year}-${parts.month}-${parts.day}T00:00:00Z`);if(Number(parts.hour)>=17)d.setUTCDate(d.getUTCDate()+1);const date=d.toISOString().slice(0,10),w=s.futureSessions?.find(x=>x.date===date);
 // An unknown most recent session cannot be skipped in favour of an older known close.
 const prior=(s.futureSessions||[]).filter(x=>x.date<=date).sort((a,b)=>b.date.localeCompare(a.date));
 let last=null;for(const x of prior){if(!x.known){last=null;break;}if(x.close<=now){last=x.date;break;}}
 return {date,scheduleKnown:!!w?.known,scheduledOpen:!!w?.segments.some(([a,b])=>a<=now&&now<b),lastCompletedSession:last};
}
return {rootOf,completed,costs,status};
});
