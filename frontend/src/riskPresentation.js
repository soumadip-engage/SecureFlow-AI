// These weights are part of the published nine-factor SecureFlow risk model.
// The backend also includes each triggered factor's maximum in ranked_factors.
export const FACTOR_MAXIMUMS = Object.freeze({
  amount_risk: 10,
  time_risk: 7,
  frequency_risk: 12,
  device_risk: 15,
  location_risk: 15,
  location_change_risk: 6,
  beneficiary_risk: 12,
  history_risk: 13,
  typical_amount_risk: 10,
});

export function presentFactors(result) {
  const breakdown = result?.risk_breakdown ?? result?.breakdown ?? {};
  if (!breakdown || typeof breakdown !== "object" || Array.isArray(breakdown)) {
    return [];
  }

  const serverMaximums = Object.fromEntries(
    (Array.isArray(result?.ranked_factors) ? result.ranked_factors : [])
      .filter((factor) => typeof factor?.factor === "string")
      .map((factor) => [factor.factor, Number(factor.maximum)])
  );

  return Object.entries(breakdown)
    .filter(([, value]) => Number.isFinite(Number(value)))
    .map(([name, rawValue]) => {
      const declaredMaximum = serverMaximums[name] || FACTOR_MAXIMUMS[name] || 100;
      const maximum = Math.max(1, declaredMaximum);
      const value = Math.min(maximum, Math.max(0, Number(rawValue)));
      return {
        name,
        value,
        maximum,
        fillPercent: (value / maximum) * 100,
        checks: result?.detailed_risk_breakdown?.[name] ?? null,
      };
    })
    .sort((a, b) => b.value - a.value)
    .slice(0, 9);
}

// /analyze-payment returns percentages in the range 0..100, or null if no
// supervised classifier is loaded. Do not multiply this value by 100 again.
export function formatMlProbability(result) {
  const raw = result?.ml_fraud_probability ?? result?.fraud_probability ?? result?.ml_probability;
  if (raw === null || raw === undefined || raw === "") {
    return "N/A — rules only";
  }
  const percent = Number(raw);
  if (!Number.isFinite(percent)) return "N/A — rules only";
  return `${Math.max(0, Math.min(100, percent)).toFixed(2)}%`;
}
