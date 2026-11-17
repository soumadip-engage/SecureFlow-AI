import test from "node:test";
import assert from "node:assert/strict";
import { formatMlProbability, presentFactors } from "./riskPresentation.js";

test("ML probability is already a percentage, including zero", () => {
  assert.equal(formatMlProbability({ ml_fraud_probability: 82.4 }), "82.40%");
  assert.equal(formatMlProbability({ ml_fraud_probability: 0 }), "0.00%");
  assert.equal(formatMlProbability({ ml_fraud_probability: null }), "N/A — rules only");
  assert.equal(formatMlProbability({}), "N/A — rules only");
});

test("a full factor score fills its bar, not 10% of the bar", () => {
  const factors = presentFactors({
    risk_breakdown: { amount_risk: 10, location_risk: 7.5, device_risk: 0 },
    detailed_risk_breakdown: { amount_risk: { high_value: 10 } },
  });
  assert.equal(factors[0].fillPercent, 100);
  assert.equal(factors[0].maximum, 10);
  assert.equal(factors[0].checks.high_value, 10);
  assert.equal(factors[1].fillPercent, 50);
  assert.equal(factors[2].fillPercent, 0);
});

test("backend factor maximum takes precedence and points cannot overflow", () => {
  const [factor] = presentFactors({
    risk_breakdown: { custom_risk: 40 },
    ranked_factors: [{ factor: "custom_risk", maximum: 20 }],
  });
  assert.equal(factor.value, 20);
  assert.equal(factor.maximum, 20);
  assert.equal(factor.fillPercent, 100);
});
