'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),M=require('../web/market-context.js');
const now=Date.parse('2026-09-08T20:10:00Z');
test('screenshot-style SPX VIX1D range is calculated and kept separate from GEX',()=>{
 const r=M.expectedRange({spot:7718.60,spotAsOf:'2026-09-08T20:00:00Z',vix1d:12.03,vixAsOf:'2026-09-08T20:00:00Z',source:'fixture',k:.9,outerK:1.4},now);
 assert.equal(r.available,true);assert.ok(Math.abs(r.move-52.64)<.02);assert.ok(Math.abs(r.outerMove-81.88)<.02);
 assert.equal(r.isGex,false);assert.equal(r.isProbability,false);assert.match(r.label,/NOT GEX/);
});
test('range fails closed without a native VIX1D observation',()=>{const r=M.expectedRange({spot:7718,spotAsOf:'2026-09-08T20:00:00Z'},now);assert.equal(r.available,false);assert.match(r.reason,/VIX1D/);});
test('future timestamps and invalid factors are rejected',()=>{
 assert.equal(M.expectedRange({spot:1,vix1d:1,spotAsOf:'2027-01-01T00:00:00Z',vixAsOf:'2026-09-08T20:00:00Z'},now).available,false);
 assert.equal(M.expectedRange({spot:1,vix1d:1,spotAsOf:'2026-09-08T20:00:00Z',vixAsOf:'2026-09-08T20:00:00Z',k:2,outerK:1},now).available,false);
});
const basis={structure:{status:'LOWER_HIGH_LOWER_LOW',sequence:[{kind:'HIGH',price:7750,confirmedAt:1}]},priceAction:{shortEvidence:['MOMENTUM_ALIGNED']}};
test('swing phase is descriptive and does not manufacture an Elliott count',()=>{const s=M.swingPhase(basis);assert.equal(s.phase,'DOWNTREND LEG CANDIDATE');assert.match(s.method,/not a certified Elliott-wave count/);});
test('flush watch requires completed downside confluence and never authorizes an order',()=>{
 const range=M.expectedRange({spot:7718.6,spotAsOf:'2026-09-08T20:00:00Z',vix1d:12.03,vixAsOf:'2026-09-08T20:00:00Z'},now);
 const r=M.riskState({review:{gates:[],decisionBasis:basis},snapshot:{spot:7665},status:'Confirmed for paper review',scenario:{direction:'bearish',breakoutObserved:true,hourAligned:true,volumeConfirmed:true,t1:7600}},range);
 assert.equal(r.code,'FLUSH_WATCH');assert.equal(r.isProbability,false);assert.equal(r.orderAuthorized,false);
});
test('data gate beats a directional setup',()=>{const r=M.riskState({review:{gates:['STALE_REFERENCE'],decisionBasis:basis},snapshot:{spot:7600},scenario:{direction:'bearish',breakoutObserved:true,hourAligned:true,volumeConfirmed:true,t1:7500}});assert.equal(r.code,'DATA_WAIT');});
