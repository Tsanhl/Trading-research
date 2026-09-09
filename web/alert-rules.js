/* Lifecycle checks around HR's existing frozen scenarios. No orders or KX authority. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.AlertRules=factory();})(globalThis,function(){
'use strict';
const signature=b=>JSON.stringify([b.t,b.o,b.h,b.l,b.c,b.v]);
function advance(previous,{review:r,bars,dataHash,now=Date.now(),resuming=false}){
 const last=bars.at(-1),events=[],track={...previous,pending:Object.fromEntries(Object.entries(previous?.pending||{}).map(([k,v])=>[k,{...v}])),ready:previous?.ready?{...previous.ready}:null,observed:bars.slice(-32).map(b=>[b.t,signature(b)]),lastBar:last?.t||null,sessionDate:r.signalDate,dataHash,ruleVersion:'rolling20-alert-review-1'};
 const emit=(type,p,reason)=>{const e={id:[r.instrument,r.feed,r.frame,type,p?.direction||'',p?.armedBar||last?.t||now].join('|'),type,symbol:r.symbol,instrument:r.instrument,feed:r.feed,horizon:r.horizon,direction:p?.direction||null,knownAt:new Date(now).toISOString(),barStart:last?.t||null,referenceAsOf:r.sourceObservedAt,dataHash,armedDataHash:p?.armedDataHash||null,frozen:p?{entry:p.entry,stop:p.stop,t1:p.t1,t2:p.t2,noChase:p.noChase,boundary:p.boundary}:null,reason,decisionBasis:r.decisionBasis||null,qualification:'RESEARCH_ONLY; KX BLOCKED',quantity:null,executable:false};events.push(e);};
 if(!last||r.gates.length){if(previous&&(Object.keys(track.pending).length||track.ready))emit('DATA_GATE',null,r.gates.join(' · ')||'No completed bar');track.pending={};track.ready=null;track.needsBaseline=true;return {track,events};}
 if(!previous||previous.needsBaseline){track.pending={};track.ready=null;track.needsBaseline=false;return {track,events};}
 if(last.t<previous.lastBar)return {track:previous,events};
 const current=new Map(track.observed),corrected=(previous.observed||[]).some(([t,s])=>current.has(t)&&current.get(t)!==s);
 if(corrected){if(Object.keys(track.pending).length||previous.lastReady)emit('CORRECTION_REVIEW_REQUIRED',null,'Provider revised an observed completed bar. Prior alert remains in the audit; confirmation withdrawn.');track.pending={};track.ready=null;track.lastReady=null;return {track,events};}
 if(last.t===previous.lastBar)return {track,events};
 const index=bars.findIndex(b=>b.t===previous.lastBar),gap=index<0||bars.length-1-index!==1;
 if(resuming||gap){if(Object.keys(track.pending).length||track.ready)emit('MONITOR_GAP',null,'A new bar appeared while paused or observations were missed. Review manually; no retrospective ready alert.');track.pending={};track.ready=null;return {track,events};}
 if(r.horizon==='INTRADAY'&&previous.sessionDate!==r.signalDate){if(track.ready)emit('CONFIRMATION_WITHDRAWN',track.ready,'The completed-bar trading session changed.');track.pending={};track.ready=null;return {track,events};}
 if(track.ready){
  const p=track.ready,sign=p.direction==='bullish'?1:-1,x=r.scenarios.find(x=>x.direction===p.direction);p.elapsed++;
  const stopped=sign===1?last.l<=p.stop:last.h>=p.stop,chased=sign*(last.c-p.noChase)>0,target=sign===1?last.h>=p.t1:last.l<=p.t1,expired=p.elapsed>(r.horizon==='SWING'?10:6),context=!x?.hourAligned||!x?.volumeConfirmed;
  if(stopped||chased||target||expired||context){emit('CONFIRMATION_WITHDRAWN',p,stopped?'Frozen stop breached.':chased?'No-chase boundary passed.':target?'First target already touched; review the dated alert, do not treat it as a fresh entry.':expired?'Completed-bar setup window expired.':'Context or participation no longer confirms.');track.ready=null;}
 }
 for(const x of r.scenarios){
  if(track.ready?.direction===x.direction)continue;
  const sign=x.direction==='bullish'?1:-1,p=track.pending[x.direction];
  if(p){
   p.elapsed=(p.elapsed||0)+1;
   if(p.elapsed>(r.horizon==='SWING'?10:6)){emit('EXPIRED',p,'Frozen setup exceeded its completed-bar window.');delete track.pending[x.direction];continue;}
   const stopped=sign===1?last.l<=p.stop:last.h>=p.stop,chased=sign*(last.c-p.noChase)>0;
   if(stopped||chased){emit(stopped?'INVALIDATED':'NO_CHASE',p,stopped?'Completed retest bar breached frozen invalidation.':'Completed close passed the frozen no-chase boundary.');delete track.pending[x.direction];continue;}
   const touched=sign===1?last.l<=p.boundary:last.h>=p.boundary,held=sign*(last.c-p.entry)>=0&&sign*(last.c-last.o)>0;
   if(touched&&held&&x.hourAligned&&x.volumeConfirmed){emit('SETUP_READY_FOR_REVIEW',p,'Later completed-bar retest held with context and participation aligned. Verify current bid/ask, event risk, options chain and account suitability. This is not permission to purchase.');track.lastReady=events.at(-1).id;track.ready={...p,id:track.lastReady};delete track.pending[x.direction];}
  }else if(x.breakoutObserved&&x.hourAligned&&x.volumeConfirmed&&!['NO CHASE','INVALIDATED'].includes(x.state)&&sign*(last.c-x.noChase)<=0){
   const frozen={direction:x.direction,entry:x.entry,stop:x.stop,t1:x.t1,t2:x.t2,noChase:x.noChase,boundary:x.boundary,armedBar:last.t,armedDataHash:dataHash,elapsed:0};track.pending[x.direction]=frozen;emit('BREAKOUT_CONFIRMED',frozen,'Completed breakout and context/participation agree. Waiting for a later completed retest; no purchase alert yet.');
  }
 }
 return {track,events};
}
return {advance};
});
