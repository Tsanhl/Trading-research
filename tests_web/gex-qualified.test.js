'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const G = require('../web/engine');

const near = (actual, expected, tolerance=1e-8) =>
  assert.ok(Math.abs(actual-expected) <= tolerance, `${actual} != ${expected}`);

function row(changes={}) {
  return {
    id:'SPXW260918C06500000', underlying:'SPX', root:'SPXW', type:'call',
    strike:6500, expiry:'2026-09-18T20:00:00Z', modelExpiryAt:'2026-09-18T20:00:00Z',
    payoffFixingAt:'2026-09-18T20:00:00Z', lastTradingAt:'2026-09-18T20:00:00Z',
    settlementType:'PM', expiryVerified:true, lifecycleVerified:true,
    lifecycleIndependentlyVerified:false, oi:1000, multiplier:100,
    gamma:.001, iv:.2, delta:.5, ...changes
  };
}

function snapshot(options) {
  return {symbol:'SPX', spot:6500, asOf:'2026-09-08T15:00:00Z', rate:.04,
    dividend:0, chainComplete:false, options};
}

test('unverified SPX lifecycle cannot enter a curve or qualified aggregate', () => {
  const result=G.gammaExposure(snapshot([row({lifecycleVerified:false})]));
  assert.equal(result.rows.length,0);
  assert.equal(result.curve.length,0);
  assert.equal(result.qualificationStatus,'UNQUALIFIED_ARITHMETIC_ONLY');
  near(result.unqualifiedNet,42_250_000,.001);
  near(result.unqualifiedGross,42_250_000,.001);
});

test('verified lifecycle admits the supplied gamma with declared GEX units', () => {
  const result=G.gammaExposure(snapshot([row()]));
  assert.equal(result.rows.length,1);
  near(result.net,42_250_000,.001);
  near(result.grossCalls,42_250_000,.001);
  assert.equal(result.grossPuts,0);
  assert.equal(result.curve.length,81);
  near(result.testedSpotRange.low,5980);
  near(result.testedSpotRange.high,7020);
  assert.equal(result.testedSpotRange.points,81);
});

test('Black-Scholes gamma agrees with central finite difference of delta', () => {
  const input=[6500,6500,10/365,.22,.04,0,'call'];
  const analytic=G.bs(...input).gamma, h=.01;
  const high=G.bs(input[0]+h,...input.slice(1)).delta;
  const low=G.bs(input[0]-h,...input.slice(1)).delta;
  near(analytic,(high-low)/(2*h),3e-9);
});

test('bounded implied-volatility solver recovers a known volatility', () => {
  const price=G.bs(6500,6500,30/365,.237,.04,0,'put').value;
  const solved=G.impliedVol(price,6500,6500,30/365,.04,0,'put');
  assert.equal(solved.converged,true);
  near(solved.iv,.237,1e-6);
});

test('curve status says no crossing instead of inventing a flip', () => {
  const result=G.gammaExposure(snapshot([row()]),{convention:'all-long'});
  assert.equal(result.curveStatus,'NO_CROSSING_IN_TESTED_RANGE');
  assert.deepEqual(result.flips,[]);
  assert.equal(result.flip,null);
});

test('model expiry controls horizon and 0DTE classification', () => {
  const contract=row({expiry:'2026-09-19T20:00:00Z',modelExpiryAt:'2026-09-08T20:00:00Z',payoffFixingAt:'2026-09-08T20:00:00Z'});
  const result=G.gammaExposure(snapshot([contract]),{maxDays:0});
  assert.equal(result.rows.length,1);
  near(result.zeroShare,100);
});
