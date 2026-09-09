/* Auditable browser research rules. Distinct from the unsealed KX release. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory(require('./engine.js'),require('./futures-tools.js'));else root.HR=factory(root.GQ,root.FuturesTools);})(globalThis,function(G,F){
'use strict';
const spec=G.SPECS, finite=G.finite, day=86400000;
function completed(s,target=s.barMinutes){
  if(s.future&&s.futureSessions)return F.completed(s,target);
  const base=s.barMinutes, cutoff=Date.parse(s.asOf), futures=!!spec[F.rootOf(s)];
  if(s.chainOnly)return {bars:[],dropped:0,reason:'SPX chain-only import: no candles supplied.'};
  const foreign=s.sessionModel&&s.sessionModel!=='us-rth'&&!futures;
  if(foreign&&target!==base)return {bars:[],dropped:0,reason:'Non-US session calendar not configured; use provider-native timeframes instead of invented US-session aggregation.'};
  if(target<base||target%base!==0)return {bars:[],dropped:0,reason:'A finer timeframe cannot be reconstructed from coarse data.'};
  const raw=s.bars.filter(b=>b.t+(base===1440?0:base*60000)<=cutoff);
  if(base===1440&&(futures||foreign)){const safe=raw.filter(b=>b.t+86400000<=cutoff);return {bars:safe,dropped:s.bars.length-safe.length,reason:'Non-US/futures daily bars require a conservative 24-hour age; exact exchange close is not certified.'};}
  if(base===1440){return {bars:raw.filter(b=>G.easternSessionEnd(b.session||G.eastern(b.t).date)<=cutoff),dropped:s.bars.length-raw.length};}
  if(target===base)return {bars:raw,dropped:s.bars.length-raw.length};
  if(target===1440&&futures)return {bars:[],dropped:0,reason:'Exact futures session calendar is not connected; no invented daily aggregation.'};
  const groups=new Map();
  for(const b of raw){
    const et=G.eastern(b.t),date=b.session||et.date;
    if(!futures&&(et.minutes<570||et.minutes>=960))continue;
    const anchor=futures?Math.floor(b.t/86400000)*86400000:b.t-(et.minutes-570)*60000;
    const start=target===1440?anchor:anchor+Math.floor((b.t-anchor)/(target*60000))*target*60000;
    const key=date+':'+start;
    if(!groups.has(key))groups.set(key,{start,date,rows:[]});groups.get(key).rows.push(b);
  }
  let dropped=0;const out=[];
  for(const {start,date,rows} of groups.values()){
    const duration=target===1440?390:target,expected=duration/base;
    if(!Number.isInteger(expected)||rows.length!==expected||rows[0].t!==start||rows.some((b,i)=>b.t!==start+i*base*60000)||start+duration*60000>cutoff){dropped++;continue;}
    out.push({t:start,o:rows[0].o,h:Math.max(...rows.map(b=>b.h)),l:Math.min(...rows.map(b=>b.l)),c:rows.at(-1).c,v:rows.reduce((n,b)=>n+b.v,0),session:date});
  }
  return {bars:out,dropped,reason:dropped?'Incomplete/gapped buckets omitted. Half-day bars are not treated as full sessions.':null};
}
function stats(s,minutes=s.barMinutes){const x=completed(s,minutes);if(s.future){const n=x.bars.length;x.bars=x.bars.filter(b=>!b.partialPeriod);x.dropped+=n-x.bars.length;}const i=G.indicators(x.bars,minutes);
 if(s.sessionModel&&s.sessionModel!=='us-rth'&&!i.insufficient){i.vwap=null;i.vwapSeries=(i.vwapSeries||[]).map(()=>null);i.pdh=null;i.pdl=null;i.orh=null;i.orl=null;i.volAnnual=null;}
 return {direction:'neutral',...i,series:x.bars,dropped:x.dropped,note:x.reason,score:G.factorScore(i)};}

function confirmedPivots(bars,width=2){
  const highs=[],lows=[];
  for(let i=width;i<bars.length-width;i++){
    const around=bars.slice(i-width,i+width+1),bar=bars[i];
    if(around.every((x,j)=>j===width||bar.h>x.h))highs.push({kind:'HIGH',price:bar.h,pivotIndex:i,confirmedIndex:i+width,confirmedAt:bars[i+width].t});
    if(around.every((x,j)=>j===width||bar.l<x.l))lows.push({kind:'LOW',price:bar.l,pivotIndex:i,confirmedIndex:i+width,confirmedAt:bars[i+width].t});
  }
  return {highs,lows};
}
function integratedContext(s,minutes=s.barMinutes){
  const st=stats(s,minutes),bars=st.series;
  if(st.insufficient||bars.length<25||!finite(st.atr)||st.atr<=0)return {available:false,reason:'At least 50 completed, validated bars with positive ATR are required.',entryAuthority:false,qualification:'KX_BLOCKED_9_GATES'};
  const pivots=confirmedPivots(bars),hs=pivots.highs.slice(-2),ls=pivots.lows.slice(-2),sequence=[...pivots.highs,...pivots.lows].sort((a,b)=>a.confirmedAt-b.confirmedAt).slice(-6);
  let structure='MIXED_OR_UNCONFIRMED';
  if(hs.length===2&&ls.length===2){if(hs[1].price>hs[0].price&&ls[1].price>ls[0].price)structure='HIGHER_HIGH_HIGHER_LOW';else if(hs[1].price<hs[0].price&&ls[1].price<ls[0].price)structure='LOWER_HIGH_LOWER_LOW';}
  const latest=bars.at(-1),prior=bars.at(-2),range=latest.h-latest.l,body=Math.abs(latest.c-latest.o),bodyFraction=range?body/range:0,atr=st.atr;
  const candle=(side)=>{const long=side==='LONG',direction=long?1:-1,closeLocation=range?(latest.c-latest.l)/range:.5,directionalClose=long?closeLocation:1-closeLocation;
    const upper=latest.h-Math.max(latest.o,latest.c),lower=Math.min(latest.o,latest.c)-latest.l,wick=long?lower:upper;
    const engulfing=long?(latest.c>latest.o&&prior.c<prior.o&&latest.o<=prior.c&&latest.c>=prior.o):(latest.c<latest.o&&prior.c>prior.o&&latest.o>=prior.c&&latest.c<=prior.o);
    const rejection=body>0&&wick>=1.5*body&&directionalClose>=.75;
    const directionalCandle=range/atr>=.8&&directionalClose>=.75&&bodyFraction>=.55;
    const atrWideClose=range/atr>=1.25&&directionalClose>=.75&&bodyFraction>=.65;
    const emaAligned=direction*(latest.c-st.ema20)>0&&direction*st.slope>=0;
    const momentum3=direction*(latest.c-bars.at(-4).c)/atr,momentum10=direction*(latest.c-bars.at(-11).c)/atr;
    const evidence=[emaAligned?'EMA20_ALIGNED':'EMA20_NOT_ALIGNED',momentum3>0&&momentum10>0?'MOMENTUM_ALIGNED':momentum3<0&&momentum10<0?'MOMENTUM_OPPOSED':'MOMENTUM_MIXED'];
    if(engulfing)evidence.push('ENGULFING');if(rejection)evidence.push('REJECTION_WICK');if(directionalCandle)evidence.push('DIRECTIONAL_CANDLE');if(atrWideClose)evidence.push('ATR_WIDE_CLOSE');
    return {side,emaAligned,momentum3Atr:momentum3,momentum10Atr:momentum10,rangeAtr:range/atr,bodyFraction,directionalCloseLocation:directionalClose,engulfing,rejection,directionalCandle,atrWideClose,evidence,entryAuthority:false};};
  const long=candle('LONG'),short=candle('SHORT');
  return {available:true,structure,pivots:{confirmedHighs:hs,confirmedLows:ls,sequence,width:2},long,short,thresholdStatus:'RESEARCH_ONLY_SEED_HYPOTHESES',sameBarEntryAllowed:false,entryAuthority:false,qualification:'KX_BLOCKED_9_GATES',lineage:'integrated/kx-reference'};
}

function quality(s,now=Date.now()){
  const reasons=[];const age=(now-Date.parse(s.spotAsOf))/1000;
  if(s.dataKind==='synthetic')reasons.push('Synthetic fixture: not market observations.');
  if(s.dataKind==='sandbox')reasons.push('Archived sandbox data: not a production market quote.');
  if(s.dataKind==='public-unverified')reasons.push('Public feed latency and entitlements are not execution-certified.');
  if(s.dataKind==='imported')reasons.push('User-imported data: source, adjustments and quotes require independent checks.');
  if(age>90)reasons.push('Reference price is older than 90 seconds.');
  if(age < -5)reasons.push('Reference price is future-dated against the computer clock.');
  if(s.continuous)reasons.push('Continuous futures reference: exact tradable contract is not known.');
  if(!s.options.length)reasons.push('No option chain: GEX is unavailable, not zero.');
  reasons.push('Exchange holidays, halts, news/event clearance and broker fills are not verified.');
  return {age,reasons,executable:false,label:s.dataKind==='synthetic'?'SYNTHETIC':s.dataKind==='sandbox'?'ARCHIVED SANDBOX':'RESEARCH DATA'};
}
function gamma(s,opts={}){
  let missingOI=0,modeled=0,modeledIV=0,missingGamma=0;const now=Date.parse(s.asOf),options=[];
  for(const source of s.options){
    if(!finite(source.oi)){missingOI++;continue;}
    const o={...source};
    const nativeFutures=!!spec[F.rootOf(s)];
    const modelAt=o.modelExpiryAt||o.payoffFixingAt||o.settlementAt||o.expiry,years=(Date.parse(modelAt)-now)/(365*day);
    if(s.symbol==='SPX'&&s.dataKind==='synthetic'){o.expiryVerified=true;o.lifecycleVerified=true;o.lifecycleSource=o.lifecycleSource||'SYNTHETIC_FIXTURE';o.modelExpiryAt=modelAt;}
    const lifecycleOK=s.symbol!=='SPX'||o.lifecycleVerified===true;
    if(!nativeFutures&&lifecycleOK&&!finite(o.iv)&&finite(o.bid)&&finite(o.ask)&&o.bid>=0&&o.ask>=o.bid&&o.quoteAsOf&&years>0){
      try{const solved=G.impliedVol((o.bid+o.ask)/2,s.spot,o.strike,years,s.rate??.04,s.dividend??0,o.type);if(solved.converged){o.iv=solved.iv;o.ivSource='LOCAL_BSM_MID_QUOTE';o.ivAsOf=o.quoteAsOf;modeledIV++;}}catch{}
    }
    if(!nativeFutures&&lifecycleOK&&(!finite(o.gamma)||!finite(o.delta))&&finite(o.iv)&&o.iv>0&&years>0){
      const b=G.bs(s.spot,o.strike,years,o.iv,s.rate??.04,s.dividend??0,o.type);
      if(!finite(o.gamma)){o.gamma=b.gamma;o.greekSource='LOCAL_BSM';o.greekAsOf=s.asOf;modeled++;}if(!finite(o.delta))o.delta=b.delta;
    }
    if(!finite(o.gamma))missingGamma++;
    options.push(o);
  }
  const r=G.gammaExposure({...s,options:spec[F.rootOf(s)]?options.map(o=>({...o,iv:null})):options},opts);
  if(spec[F.rootOf(s)]){r.curve=[];r.flips=[];r.flip=null;r.modelNetAtSpot=null;}
  return {...r,available:r.rows.length>0,missingOI,modeled,modeledIV,missingGamma:r.missingGamma,normalizedOptions:options,
    note:'Dollar delta-notional change per 1% spot move. Dealer positioning is assumed, not observed.'};
}
function repriceGamma(s,{spot,spotAsOf,source,now=Date.now(),convention='call-put',maxDays=30}={}){
 const fail=reason=>({available:false,reason});const captured=Date.parse(s.asOf),at=Date.parse(spotAsOf);
 if(s.symbol!=='SPX'||!s.options?.length)return fail('A native SPX/SPXW chain is required.');
 if(!finite(spot)||spot<=0||!finite(at)||at<=captured||at>now+5000||!String(source||'').trim())return fail('Use a positive, newer timestamped SPX observation and name its source. Future timestamps are rejected.');
 const old=gamma(s,{convention,maxDays:3650}),options=[];let expired=0;
 for(const o of old.normalizedOptions){
  if(o.underlying!=='SPX'||!['SPX','SPXW'].includes(o.root)||o.multiplier!==100||o.lifecycleVerified!==true)return fail('Every repriced row requires native identity and qualified lifecycle metadata.');
  const modelAt=o.modelExpiryAt||o.payoffFixingAt||o.settlementAt||o.expiry,years=(Date.parse(modelAt)-at)/(365*day);
  if(!finite(years))return fail('Malformed payoff-fixing time.');if(years<=0){expired++;continue;}
  if(!finite(o.oi)||!finite(o.iv)||o.iv<=0)return fail('Every surviving row requires OI and IV. Fixed supplied gamma cannot be repriced without IV.');
  const b=G.bs(spot,o.strike,years,o.iv,s.rate??.04,s.dividend??0,o.type);
  options.push({...o,gamma:b.gamma,delta:b.delta,originalGamma:o.gamma,originalGreekAsOf:o.greekAsOf||null,greekSource:'LOCAL_BSM_OLDER_CHAIN_REPRICE',greeksSource:'LOCAL_BSM_OLDER_CHAIN_REPRICE',greekAsOf:spotAsOf,ivAsOf:o.ivAsOf||s.asOf});
 }
 if(old.missingOI)return fail('Missing OI cannot be converted to zero or omitted from a repriced scenario.');
 if(!options.length)return fail('All contracts have expired at the newer observation. Import a newer chain.');
 const provenance={kind:'REPRICED_OLDER_CHAIN_SCENARIO',chainCapturedAt:s.asOf,chainSpot:s.spot,chainSpotAsOf:s.spotAsOf,chainSource:s.source,chainSourceHash:s.sourceHash||null,oiAsOf:s.oiAsOf,ivPolicy:'Original IV held fixed by strike; missing IV may be solved only from original timestamped quotes',spotSource:String(source).trim(),spotAsOf,modelAt:spotAsOf,expiredRows:expired,dataKind:s.dataKind,positioningAssumption:convention,rate:s.rate??.04,dividend:s.dividend??0,maxDays,disclaimer:'Modelled using older chain observations; not a newly observed live chain'};
 const snapshot={...s,options,spot,spotAsOf,asOf:spotAsOf,repricing:provenance};
 return {available:true,snapshot,provenance,result:gamma(snapshot,{convention,maxDays})};
}
function plans(s,prefs={}){
  const base=s.barMinutes,frame=base<=15?15:base,st=stats(s,frame),bars=st.series;
  const q=quality(s,prefs.now??Date.now()),tick=spec[F.rootOf(s)]?.tick||.01,kx=integratedContext(s,frame);
  const snap=(x,up=false)=>Number(((up?Math.ceil(x/tick-1e-9):Math.floor(x/tick+1e-9))*tick).toFixed(8));
  const hourInput={...s,asOf:bars.length?new Date(bars.at(-1).t).toISOString():s.asOf};
  const hour=base<=60?stats(hourInput,60):{insufficient:true};
  const swing=base>=1440,context=swing?stats({...s,bars:s.bars.slice(0,-1)},base):hour;
  const contextLabel=swing?'prior completed daily trend':'prior completed 1H';
  const failure=s.sessionModel&&s.sessionModel!=='us-rth'&&!spec[F.rootOf(s)]?'SESSION MODEL NOT QUALIFIED':prefs.eventFreeze?'EVENT FREEZE':bars.length<51||st.insufficient||!finite(st.atr)||st.atr<=0?'INSUFFICIENT DATA':null;
  if(failure)return {action:failure,frame,quality:q,stats:st,hour,kxContext:kx,scenarios:[],executable:false};
  const past=bars.slice(-21,-1),latest=bars.at(-1),previous=bars.at(-2),a=st.atr;
  const high=Math.max(...past.map(b=>b.h)),low=Math.min(...past.map(b=>b.l));
  const scenarios=['bullish','bearish'].map(direction=>{
    const up=direction==='bullish',sign=up?1:-1,boundary=up?high:low,entry=snap(boundary+sign*tick,up);
    const stop=snap(up?Math.min(...bars.slice(-7,-1).map(b=>b.l))-.1*a:Math.max(...bars.slice(-7,-1).map(b=>b.h))+.1*a,!up);
    const risk=Math.abs(entry-stop),t1=snap(entry+sign*2*risk,up),t2=snap(entry+sign*3*risk,up),noChase=snap(entry+sign*.35*a,up);
    const broke=up?latest.c>boundary&&previous.c<=boundary:latest.c<boundary&&previous.c>=boundary;
    const hourAligned=!context.insufficient&&context.direction===direction;
    const volume=finite(st.rvol)&&st.rvol>=1.1;
    let state='WATCH';
    if(sign*(s.spot-stop)<=0)state='INVALIDATED';
    else if(sign*(s.spot-noChase)>0)state='NO CHASE';
    else if(broke&&hourAligned&&volume)state='ARMED · WAIT FOR RETEST';
    else if(broke)state='WAIT FOR CONFIRMATION';
    const currentRisk=sign*(s.spot-stop),remaining=currentRisk>0?sign*(t1-s.spot)/currentRisk:null;
    return {direction,state,boundary,entry,stop,t1,t2,noChase,risk,remainingRR:remaining,breakoutObserved:broke,hourAligned,volumeConfirmed:volume,
      trigger:`Completed ${frame===1440?'daily':frame+'m'} close ${up?'above':'below'} ${boundary.toFixed(2)}, then a later retest hold; ${contextLabel} and participation must agree.`,
      targetBasis:'T1=2R and T2=3R hypothetical planning objectives, not independently observed support/resistance.',
      timeStop:frame===1440?'10 daily bars':'6 completed signal bars',executable:false};
  });
  return {action:'WAIT · VERIFY DATA',frame,quality:q,stats:st,hour,context,contextLabel,kxContext:kx,scenarios,executable:false,
    rules:'Experimental rolling-20-bar breakout hypotheses. Separate from the unsealed KX system; GEX is context, not an entry trigger.'};
}
function optionIdeas(s,{direction='bullish',style='spread',equity=100000,riskPct=.25,fee=.65,slip=.02}={}){
  if(!['bullish','bearish'].includes(direction)||!['spread','single'].includes(style))throw Error('Invalid option direction/style');
  for(const x of [equity,riskPct,fee,slip])if(!finite(x))throw Error('Nonfinite option risk input');
  if(equity<=0||riskPct<=0||riskPct>5||fee<0||slip<0)throw Error('Invalid risk, fee or slippage');
  const type=direction==='bullish'?'call':'put',now=Date.parse(s.asOf),normalized=gamma(s).normalizedOptions;
  const pool=normalized.filter(o=>{const days=(Date.parse(o.expiry)-now)/day,mid=(o.bid+o.ask)/2;return o.type===type&&days>=7&&days<=21&&o.multiplier===100&&finite(o.bid)&&finite(o.ask)&&o.bid>0&&o.ask>=o.bid&&o.oi>=100&&(o.volume??0)>=20&&mid>0&&(o.ask-o.bid)/mid<=.15&&finite(o.delta)&&(type==='call'?o.delta>0:o.delta<0);});
  const buys=pool.filter(o=>Math.abs(o.delta)>=.45&&Math.abs(o.delta)<=.65).sort((a,b)=>Math.abs(Math.abs(a.delta)-.55)-Math.abs(Math.abs(b.delta)-.55));
  const ideas=[];
  for(const buy of buys){
    const sells=style==='single'?[null]:pool.filter(o=>o.expiry===buy.expiry&&(type==='call'?o.strike>buy.strike:o.strike<buy.strike)).sort((a,b)=>Math.abs(a.strike-buy.strike)-Math.abs(b.strike-buy.strike));
    for(const sell of sells){
      const entry=buy.ask+slip-(sell?Math.max(0,sell.bid-slip):0),width=sell?Math.abs(sell.strike-buy.strike):null;
      if(entry<=0||(sell&&entry>=width))continue;
      const legs=sell?2:1,fees=legs*fee*2; // entry and exit commissions, conservatively
      const maxLoss=entry*100+fees,maxGain=sell?(width-entry)*100-fees:type==='call'?Infinity:buy.strike*100-maxLoss;
      if(maxGain<=0)continue;
      const breakeven=buy.strike+(type==='call'?1:-1)*(entry+fees/100),budget=equity*riskPct/100;
      const warnings=['Indicative chain candidate at snapshot cutoff, NOT a current limit order.','Model delta may replace missing provider delta. Same-expiry standard 100-share contracts only.','American exercise, dividends, assignment and pin risk require broker review; close spreads before expiry when appropriate.'];
      if(!buy.quoteAsOf||sell&&!sell.quoteAsOf)warnings.push('Bid/ask quote timestamp is unknown; cannot certify an entry.');
      if(!buy.expiryVerified||sell&&!sell.expiryVerified)warnings.push('Exact expiry/settlement is not independently verified.');
      ideas.push({ok:true,label:style==='single'?`Long ${type}`:type==='call'?'Bull call debit spread':'Bear put debit spread',type,style,buy,sell,entry,width,fees,legs,maxLoss,maxGain,breakeven,budget,contracts:Math.max(0,Math.floor(budget/maxLoss)),warnings,paperOnly:true,executable:false});break;
    }
    if(ideas.length>=3)break;
  }
  return {ideas,reason:ideas.length?null:'No 7–21 DTE standard contract passes delta, OI, volume and spread filters. Use the manual payoff calculator; no contract or quote is invented.'};
}
function manualOption({type='call',buyStrike,sellStrike=null,debit,fees=2.6,equity=100000,riskPct=.25}){
  if(!['call','put'].includes(type)||![buyStrike,debit,fees,equity,riskPct].every(finite)||buyStrike<=0||debit<=0||fees<0||equity<=0||riskPct<=0||riskPct>5)throw Error('Use positive strikes/debit/account and 0–5% risk.');
  const sell=sellStrike!=null&&sellStrike!=='';
  if(sell&&(!finite(sellStrike)||sellStrike<=0||(type==='call'?sellStrike<=buyStrike:sellStrike>=buyStrike)))throw Error('Call spread: sell strike above buy strike. Put spread: sell strike below buy strike.');
  const width=sell?Math.abs(sellStrike-buyStrike):null;
  if(sell&&debit>=width)throw Error('Debit must be less than spread width.');
  const maxLoss=debit*100+fees,maxGain=sell?(width-debit)*100-fees:type==='call'?Infinity:buyStrike*100-maxLoss;
  if(maxGain<=0)throw Error('Costs leave no possible positive payoff.');
  return {ok:true,type,buy:{type,strike:buyStrike},sell:sell?{type,strike:sellStrike}:null,entry:debit,fees,width,maxLoss,maxGain,breakeven:buyStrike+(type==='call'?1:-1)*(debit+fees/100),budget:equity*riskPct/100,contracts:Math.floor(equity*riskPct/100/maxLoss),executable:false};
}
function optionReview(s,{direction='bullish',style='spread',budget=null,fee=.65,slip=.02,now=Date.now(),eventFreeze=false}={}){
  if(budget!==null&&(!finite(budget)||budget<0))throw Error('Option premium budget must be a nonnegative USD amount, or blank.');
  const unsupported=!['equity','etf'].includes(s.assetClass)||s.sessionModel!=='us-rth';
  if(eventFreeze||unsupported)return {ideas:[],rejected:0,reason:eventFreeze?'Manual event freeze suppresses option candidates.':'This candidate screen supports standard US stock/ETF options. SPX settlement and futures options require their own qualified instruments.'};
  const ticker=s.symbol.split(':').at(-1),rejected=[];
  const valid=(s.options||[]).filter(o=>{
    if(!finite(Date.parse(o.expiry))){rejected.push(o.id);return false;}
    const m=String(o.id||'').match(/^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$/),et=G.eastern(Date.parse(o.expiry)).date.replaceAll('-','').slice(2);
    const ok=m&&m[1]===ticker&&(!o.underlying||o.underlying===s.symbol||o.underlying===ticker)&&m[2]===et&&m[3]===(o.type==='call'?'C':'P')&&Math.abs(Number(m[4])/1000-o.strike)<1e-8&&o.multiplier===100;
    if(!ok)rejected.push(o.id);return ok;
  });
  const found=optionIdeas({...s,options:valid},{direction,style,equity:1,riskPct:1,fee,slip});
  const ideas=found.ideas.map(i=>{
    const legs=[i.buy,i.sell].filter(Boolean),gates=[];
    if(['synthetic','sandbox'].includes(s.dataKind))gates.push('NON_MARKET_INPUT');
    if(!finite(Date.parse(s.spotAsOf))||now-Date.parse(s.spotAsOf)>20*60000||Date.parse(s.spotAsOf)>now+5000)gates.push('UNDERLYING_REFERENCE_NOT_CURRENT');
    for(const leg of legs){
      const t=Date.parse(leg.quoteAsOf),age=(now-t)/1000;
      if(!finite(t))gates.push('QUOTE_TIME_UNKNOWN');else if(age>60||age < -5)gates.push('QUOTE_NOT_CURRENT');
      if(!leg.expiryVerified)gates.push('EXPIRY_METADATA_UNVERIFIED');
      if(Date.parse(leg.expiry)<=now)gates.push('EXPIRED_CONTRACT');
      if((Date.parse(leg.expiry)-now)/day<7||(Date.parse(leg.expiry)-now)/day>21)gates.push('OUTSIDE_CURRENT_7_21_DTE');
      if(!/^\d{4}-\d{2}-\d{2}/.test(leg.oiAsOf||s.oiAsOf||''))gates.push('OI_DATE_UNKNOWN');
    }
    if(legs.length===2&&Math.abs(Date.parse(legs[0].quoteAsOf)-Date.parse(legs[1].quoteAsOf))>5000)gates.push('LEG_QUOTES_NOT_SYNCHRONIZED');
    const unique=[...new Set(gates)],contracts=budget===null?null:Math.max(0,Math.floor(budget/i.maxLoss));
    return {...i,budget,contracts,quantityApproved:false,quoteGates:unique,status:unique.length?'THEORETICAL / HISTORICAL ONLY':'QUOTED RESEARCH — VERIFY SETUP',maxPayoffRR:finite(i.maxGain)?i.maxGain/i.maxLoss:null,knownAt:new Date(now).toISOString(),sourceObservedAt:s.spotAsOf,qualification:'RESEARCH_ONLY; KX BLOCKED',executable:false};
  });
  return {ideas,rejected:rejected.length,reason:ideas.length?null:!(s.options||[]).length?'No option chain is attached. Fetch an optional public chain or import your own timestamped standard contracts. Strikes and premiums are not invented.':!valid.length?'No contract has matching underlying, OCC identity, expiry, type, strike and multiplier. Use an exact standard-contract export.':found.reason};
}
function futureRisk(v){
  if(!spec[v.symbol])throw Error('Choose ES, MES, NQ or MNQ.');
  if(!['long','short'].includes(v.side))throw Error('Direction must be long or short.');
  const tick=spec[v.symbol].tick;
  if([v.entry,v.stop].some(x=>!finite(x)||Math.abs(x/tick-Math.round(x/tick))>1e-7))throw Error('Entry and stop must lie on the 0.25-point tick grid.');
  if(v.side==='long'&&v.stop>=v.entry||v.side==='short'&&v.stop<=v.entry)throw Error('Stop must be below a long entry or above a short entry.');
  const r=G.riskSize(v);let affordable=null;
  if(v.margin!=null&&v.margin!==''){if(!finite(v.margin)||v.margin<=0)throw Error('Margin must be positive, or left blank as unknown.');affordable=Math.floor(v.equity/v.margin);}
  return {...r,riskOnlyContracts:r.contracts,marginCap:affordable,contracts:affordable==null?null:Math.min(r.contracts,affordable),executable:false,
    note:affordable==null?'Risk-only size is shown. Margin unknown: final contract size is BLOCKED.':'Margin is a user assumption, not broker-verified. Stop/gap losses may exceed this estimate.'};
}
return {completed,stats,confirmedPivots,integratedContext,quality,gamma,repriceGamma,plans,optionIdeas,optionReview,manualOption,futureRisk};
});
