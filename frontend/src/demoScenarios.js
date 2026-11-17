// Fill input fields only. No scenario contains a preset risk score or decision;
// FastAPI evaluates every example using the selected user's actual demo history.
export function buildDemoScenario(kind, profile = {}, transactions = [], timestamp = "") {
  const average = Number(profile?.average_amount);
  const usualAmount = Number.isFinite(average) && average > 0 ? average : 2500;
  const knownDevice = (profile?.known_devices ?? []).find((value) =>
    typeof value === "string" && value.trim() && value.toLowerCase() !== "unknown-device"
  ) ?? "UNKNOWN-DEVICE";
  const usualLocation = (profile?.familiar_locations ?? []).find(Boolean) ?? "";
  const previousUpi = transactions.find((tx) =>
    typeof tx.recipient_upi_id === "string" && tx.recipient_upi_id.trim() &&
    !["BLOCK", "BLOCKED", "VERIFY", "HOLD"].includes(
      String(tx.status ?? tx.decision ?? "").toUpperCase()
    )
  )?.recipient_upi_id ?? "sample-recipient@demo";

  const base = {
    amount: String(Math.max(100, Math.round(usualAmount))),
    transaction_category: "Shopping",
    transaction_purpose: "Shopping",
    recipient_upi_id: previousUpi,
    device: knownDevice,
    ip_address: "",
    ip_location: "",
    location_name: usualLocation,
    latitude: "",
    longitude: "",
    transaction_time: timestamp,
  };

  if (kind === "new-device") {
    return { ...base, device: "SIM-UNRECOGNIZED-DEVICE" };
  }
  if (kind === "high-value") {
    return {
      ...base,
      amount: String(Math.round(Math.max(50000, usualAmount * 12))),
      recipient_upi_id: "first-time-recipient@demo",
      device: "SIM-UNRECOGNIZED-DEVICE",
      location_name: "Mumbai, Maharashtra",
    };
  }
  return base;
}
