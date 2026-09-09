/* Expected-range and structural risk labels for the shared Hub review. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.MarketContext=factory();})(globalThis,function(){
'use strict';
const finite=v=>typeof v==='number'&&Number.isFinite(v);
function expectedRange(input={},now=Date.now()){
 const spot=Number(input.spot),vix1d=Number(input.vix1d),k=Number(input.k??.9),outerK=Number(input.outerK??1.4);
 const spotAt=Date.parse(input.spotAsOf),vixAt=Date.parse(input.vixAsOf);
 const unavailable=reason=>({available:false,reason,method:'SPX × VIX1D / 100 / √252 × K',isGex:false,isProbability:false});
 if(!finite(spot)||spot<=0)return unavailable('Enter a positive timestamped SPX observation.');
 if(!finite(vix1d)||vix1d<=0||vix1d>300)return unavailable('Enter a positive timestamped VIX1D observation. VIX or price alone is not substituted.');
 if(!finite(k)||!finite(outerK)||k<=0||outerK<=k||outerK>10)return unavailable('Use positive range factors with the outer factor above the inner factor.');
 if(!finite(spotAt)||!finite(vixAt))return unavailable('Both observations require ISO timestamps with explicit timezones.');
 if(spotAt>now+5000||vixAt>now+5000)return unavailable('Future-dated observations are rejected.');
 const dailyOneSigma=spot*vix1d/100/Math.sqrt(252),move=dailyOneSigma*k,outerMove=dailyOneSigma*outerK;
 const oldest=Math.min(spotAt,vixAt),ageSeconds=Math.max(0,(now-oldest)/1000);
 return {available:true,method:'SPX × VIX1D / 100 / √252 × K',isGex:false,isProbability:false,
  spot,vix1d,k,outerK,center:spot,lower:spot-move,upper:spot+move,outerLower:spot-outerMove,outerUpper:spot+outerMove,
  dailyOneSigma,move,outerMove,spotAsOf:new Date(spotAt).toISOString(),vixAsOf:new Date(vixAt).toISOString(),
  source:String(input.source||'User-entered observations').slice(0,160),ageSeconds,
  freshness:ageSeconds<=20*60?'OBSERVATIONS_WITHIN_20_MINUTES':'STALE_FOR_ENTRY_TIMING',
  label:'MODELLED EXPECTED RANGE / TR PROJECTION — NOT GEX OR A PROBABILITY'};
}
function swingPhase(basis={}){
 const structure=basis.structure||{},sequence=Array.isArray(structure.sequence)?structure.sequence:[];
 const latest=sequence.at(-1),status=structure.status||'UNKNOWN';
 let phase='ALTERNATE COUNT UNRESOLVED',instruction='Wait for another confirmed swing and completed-bar break.';
 if(status==='HIGHER_HIGH_HIGHER_LOW'){
  phase=latest?.kind==='HIGH'?'PULLBACK / TRIM WATCH':'UPTREND LEG CANDIDATE';
  instruction=latest?.kind==='HIGH'?'Protect gains if follow-through fails; do not call the high in advance.':'The last confirmed higher low is structural invalidation; require a later completed-bar trigger.';
 }else if(status==='LOWER_HIGH_LOWER_LOW'){
  phase=latest?.kind==='LOW'?'BOUNCE / COVER-TRIM WATCH':'DOWNTREND LEG CANDIDATE';
  instruction=latest?.kind==='LOW'?'Do not chase the flush; reassess at the frozen no-chase level.':'The last confirmed lower high is structural invalidation; require a later completed-bar trigger.';
 }else if(status==='MIXED_OR_UNCONFIRMED'){
  phase='TRADING RANGE / MIXED SWINGS';instruction='Treat both directions as alternatives until a completed break and later retest hold.';
 }
 return {status,phase,instruction,sequence:sequence.slice(-6),method:'Confirmed width-2 pivots; descriptive swing phase, not a certified Elliott-wave count.'};
}
function riskState(card,range=null){
 const r=card?.review||{},x=card?.scenario,s=card?.snapshot||{},swing=swingPhase(r.decisionBasis||{}),spot=Number(s.spot);
 let code='WAIT',label='WAIT',reason=x?'Waiting for the defined completed-bar transition.':'No single conditional setup is selected.';
 if(r.gates?.length){code='DATA_WAIT';label='WAIT · DATA';reason='Data, session or qualification gates block current entry timing.';}
 else if(x){code=card.status==='Confirmed for paper review'?'PAPER_REVIEW':'ENTRY_WATCH';label=code==='PAPER_REVIEW'?'REVIEW PAPER ORDER':'ENTRY WATCH';reason=x.trigger||reason;}
 const downStructure=swing.status==='LOWER_HIGH_LOWER_LOW',shortMomentum=(r.decisionBasis?.priceAction?.shortEvidence||[]).includes('MOMENTUM_ALIGNED');
 const flushBand=range?.available&&finite(spot)&&spot<=range.lower;
 if(!r.gates?.length&&x?.direction==='bearish'&&x.breakoutObserved&&x.hourAligned&&x.volumeConfirmed&&downStructure&&shortMomentum&&(!range?.available||flushBand)){
  code='FLUSH_WATCH';label='FLUSH WATCH';reason='Completed downside break, lower-high/lower-low structure, momentum and participation agree'+(range?.available?' below the inner expected-range band.':'. Expected-range confirmation is unavailable.');
 }
 const longTrim=x?.direction==='bullish'&&finite(spot)&&(spot>=x.t1||(range?.available&&spot>=range.outerUpper));
 const shortTrim=x?.direction==='bearish'&&finite(spot)&&(spot<=x.t1||(range?.available&&spot<=range.outerLower));
 if(!r.gates?.length&&(longTrim||shortTrim)){
  code='TRIM_WATCH';label='TRIM / DO NOT CHASE';reason='Price reached a frozen first target or the outer expected-range band. Reassess or reduce paper exposure; this is not an automatic exit.';
 }
 return {code,label,reason,swing,expectedRangeUsed:!!range?.available,isProbability:false,orderAuthorized:false};
}
return {expectedRange,swingPhase,riskState};
});
