/* Comparison and data gates around the existing HR engine; no order capability. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory(require('./research-engine.js'),require('./futures-tools.js'));else root.MonitorTools=factory(root.HR,root.FuturesTools);})(globalThis,function(HR,F){
'use strict';
function netRR(entry,stop,target,cost){
 if(![entry,stop,target,cost].every(Number.isFinite)||cost<0||entry===stop)return null;
 const side=Math.sign(entry-stop),risk=Math.abs(entry-stop)+cost,reward=side*(target-entry)-cost;
 return {risk,reward,ratio:reward/risk,breakEvenWinFraction:reward>0?risk/(risk+reward):null};
}
function accept(request,current,s){return request.generation===current.generation&&request.provider===current.provider&&s?.symbol===request.symbol&&s.feed===request.provider&&s.barMinutes===request.minutes;}
function pairReference(s,q){
 if(s.barMinutes<1440||!q||!s.instrumentId||['instrumentId','feed','currency','sessionModel','adjustment','dataKind'].some(k=>s[k]!==q[k])||!Number.isFinite(q.spot)||q.spot<=0||Date.parse(q.spotAsOf)<=Date.parse(s.spotAsOf))return s;
 const times=[s.asOf,q.asOf,q.spotAsOf].map(Date.parse);
 if(!times.every(Number.isFinite)||times[2]>times[1])return s;
 const asOf=new Date(Math.max(times[0],times[1])).toISOString();
 return {...s,spot:q.spot,spotAsOf:q.spotAsOf,asOf,fetchAsOf:asOf,referencePair:{policy:'Same instrument/provider/currency/session/adjustments; current intraday reference with untouched daily bars',originalDailySpot:s.spot,originalDailySpotAsOf:s.spotAsOf,originalDailyCaptureAt:s.asOf,originalDailyFetchAt:s.fetchAsOf||s.asOf,referenceSource:q.source,referenceAsOf:q.spotAsOf,referenceCapturedAt:q.asOf,referenceFetchedAt:q.fetchAsOf||q.asOf},warnings:[...(s.warnings||[]),`Daily bars retained (capture ${s.asOf}); price reference separately paired from this provider’s intraday observations (capture ${q.asOf}).`]};
}
function decisionBasis(p,brief,now,eventFreeze){
 const k=p.kxContext||{},at=Date.parse(brief?.reviewedAt),age=now-at;
 const current=Number.isFinite(age)&&age>=0&&age<=86400000;
 return {schema:'hub.decision-basis.v1',ruleVersion:'rolling20-review-1',
  structure:{status:k.available?k.structure:'UNKNOWN',context:p.contextLabel||'Completed trend context unavailable',sequence:k.pivots?.sequence||[],
   method:'Confirmed swings + completed higher-timeframe trend; no Elliott wave or head-and-shoulders detector'},
  priceAction:{status:p.scenarios?.length?'CONDITIONAL':'UNAVAILABLE',
   method:'Completed breakout, later retest, participation, invalidation and no-chase; not a full Brooks pattern system',
   longEvidence:k.long?.evidence||[],shortEvidence:k.short?.evidence||[]},
  macro:{status:eventFreeze?'MANUAL_FREEZE':current?'DATED_PARTIAL_CONTEXT':'UNKNOWN_OR_STALE',
   reviewedAt:brief?.reviewedAt||null,coverage:current?String(brief.coverage||'Partial; completeness unverified'):'No current reviewed briefing',
   summary:current?String(brief.summary||'').slice(0,1200):null,
   events:current?(Array.isArray(brief.events)?brief.events:[]).slice(0,8).map(e=>({when:e.when,title:e.title,implication:e.implication,url:e.url})):[],
   directionAuthority:false,calendarComplete:false},
  sourceRoles:{Kevin:'Structure reference; generic confirmed-pivot context implemented',Brooks:'Price-action reference; breakout/retest and risk controls overlap, full pattern rules not implemented',Edgerunner:'Macro context reference; dated reporting does not create an entry signal'},
  strategyQualified:false,entryAuthority:false};
}
function optionsForPlan(s,card,opts={}){
 const r=card?.review,x=card?.scenario,underlying=card?.snapshot;
 if(!s?.instrumentId||!underlying||['instrumentId','feed','dataKind','currency','adjustment','sessionModel'].some(k=>s[k]!==underlying[k]))return {ideas:[],reason:'WAIT — option chain and underlying plan must match instrument, feed, currency, session and adjustment.'};
 if(!x||r?.gates?.length||!['bullish','bearish'].includes(x.direction))return {ideas:[],reason:'WAIT — no usable underlying direction. Resolve the underlying stock plan’s data and setup gates first.'};
 const result=HR.optionReview(s,{...opts,direction:x.direction});
 return {...result,underlyingPlan:{instrument:r.instrument,direction:x.direction,entry:x.entry,stop:x.stop,knownAt:r.knownAt,decisionBasis:r.decisionBasis},
  ideas:result.ideas.map(i=>({...i,underlyingDirection:x.direction,decisionBasis:r.decisionBasis,underlyingStatus:card.status}))};
}
function review(s,{now=Date.now(),cost=.1,session={},eventFreeze=false,feedError='',brief=null}={}){
 if(!Number.isFinite(cost)||cost<0||cost>100)throw Error('Enter a finite round-trip cost assumption from 0 to 100 per share.');
 const future=!!s.future,fc=future?F.costs(s):null; if(fc)cost=fc.points; if(future)session=F.status(s,now);
 const p=HR.plans(s,{eventFreeze,now}),last=p.stats?.series?.at(-1),quoteAge=(now-Date.parse(s.spotAsOf))/1000;
 const signalClose=last?(last.closeAt||last.t+(p.frame===1440?0:(last.durationMinutes||p.frame)*60000)):null;
 const barDate=last?.session||(last?new Intl.DateTimeFormat('en-CA',{timeZone:s.exchangeTimezone||'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(last.t)):null);
 const gates=[],swing=s.barMinutes>=1440;
 if(['sandbox','synthetic'].includes(s.dataKind))gates.push('NON_MARKET_INPUT');
 if(!future&&(s.sessionModel!=='us-rth'||!['equity','etf'].includes(s.assetClass)))gates.push('UNSUPPORTED_SESSION_OR_INSTRUMENT');
 if(future){if(!s.future.exact)gates.push('CONTINUOUS_REFERENCE_ONLY');if(Date.parse(s.future.expiryAt)<=now)gates.push('CONTRACT_EXPIRED');if(s.feedCapabilities?.realtimeVerified!==true)gates.push('FUTURES_ENTRY_TIMING_UNVERIFIED');}
 if(!session.scheduleKnown)gates.push('CALENDAR_UNKNOWN');
 if(!swing&&session.scheduleKnown&&!session.scheduledOpen)gates.push(future?'OUTSIDE_QUALIFIED_CME_SESSION':'OUTSIDE_SCHEDULED_RTH');
 if(!Number.isFinite(quoteAge)||quoteAge < -5)gates.push('INVALID_QUOTE_TIME');
 else if(quoteAge>20*60)gates.push('STALE_REFERENCE');
 if(!last)gates.push('MISSING_COMPLETED_BARS');
 else if(swing){if(!session.lastCompletedSession||barDate!==session.lastCompletedSession)gates.push('DAILY_SESSION_NOT_CURRENT');}
 else if(now-signalClose>Math.max(10,p.frame*2)*60000||signalClose>now)gates.push('STALE_OR_FUTURE_SIGNAL_BAR');
 if(feedError)gates.push('FEED_ERROR_PREVIOUS_DATA_RETAINED');
 if(eventFreeze)gates.push('MANUAL_EVENT_FREEZE');
 const scenarios=p.scenarios.map(x=>({...x,netT1:netRR(x.entry,x.stop,x.t1,cost),netT2:netRR(x.entry,x.stop,x.t2,cost),
  costPerShare:future?null:cost,costPerContract:fc?.roundTripUsd??null,riskPerContract:fc?(Math.abs(x.entry-x.stop)*fc.pointValue+fc.roundTripUsd):null,entryRange:[Math.min(x.entry,x.noChase),Math.max(x.entry,x.noChase)],
  grossT1R:x.risk>0?Math.abs(x.t1-x.entry)/x.risk:null,
  rrAtNoChase:netRR(x.noChase,x.stop,x.t1,cost),
  status:gates.length?'WAIT — DATA GATE':x.state,quantity:null,executable:false}));
 return {schema:'hub.monitor-review.v1',ruleVersion:'rolling20-review-1',instrument:s.instrumentId||s.symbol,symbol:s.symbol,
  source:s.source,feed:s.feed,dataKind:s.dataKind,sourceObservedAt:s.spotAsOf,sourceFetchedAt:s.fetchAsOf||s.asOf,referencePair:s.referencePair||null,
  knownAt:new Date(now).toISOString(),signalBarStart:last?.t||null,signalBarClose:signalClose,signalDate:barDate,
  horizon:swing?'SWING':'INTRADAY',frame:p.frame,quoteAgeSeconds:quoteAge,
  status:gates.length?'WAIT — DATA GATE':'PUBLIC RESEARCH — CHECK TRIGGER',gates,
  sessionModel:s.sessionModel,adjustment:s.adjustment,calendarVersion:s.calendarVersion||null,sessionEvidence:session,
  future:s.future||null,futuresCosts:fc,ruleScore:p.stats?.score?.score??p.stats?.score?.total??0,freshness:gates.some(g=>/STALE|TIME|CALENDAR|CONTINUOUS|NON_MARKET/.test(g))?'UNQUALIFIED':'OBSERVATION_WITHIN_AGE_LIMIT',
  context:p.contextLabel||'prior completed 1H',trend:p.stats?.direction,scenarios,decisionBasis:decisionBasis(p,brief,now,eventFreeze),
  qualification:'RESEARCH_ONLY; KX BLOCKED',costAssumption:cost,quantity:null,executable:false,
  missingGates:['Current bid/ask and slippage','Complete event/earnings coverage','Independent strategy evidence','Explicit account risk and holdings'],
  note:'T1/T2 are modeled 2R/3R objectives. Their existence does not demonstrate an edge; retest confirmation and fills are not simulated.'};
}
function card(r,track,active=false){
 const ready=active&&!r.gates.length?track?.ready:null,pending=active&&!r.gates.length?Object.values(track?.pending||{}):[];
 const candidates=r.scenarios.filter(x=>x.hourAligned&&!['INVALIDATED','NO CHASE'].includes(x.state));
 const selected=ready|| (pending.length===1?pending[0]:candidates.length===1?candidates[0]:null);
 const base=selected?r.scenarios.find(x=>x.direction===selected.direction):null;
 const cost=r.costAssumption,x=selected?{...base,...selected,netT1:netRR(selected.entry,selected.stop,selected.t1,cost),netT2:netRR(selected.entry,selected.stop,selected.t2,cost)}:null;
 return {review:r,scenario:x,direction:!x||r.gates.length?'WAIT':x.direction==='bullish'?'BUY setup':'SELL setup',status:r.gates.length?'WAIT — DATA GATE':ready?'Confirmed for paper review':x?'Waiting for completed retest':'WAIT — conflicting or unconfirmed context',priority:ready?0:x?1:2,score:r.ruleScore||0,netRR:x?.netT1?.ratio??-Infinity};
}
function rankCards(cards){return [...cards].sort((a,b)=>a.priority-b.priority||b.score-a.score||b.netRR-a.netRR);}
return {netRR,accept,pairReference,review,card,rankCards,decisionBasis,optionsForPlan};
});
