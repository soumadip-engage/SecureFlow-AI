import test from "node:test";
import assert from "node:assert/strict";
import { buildDemoScenario } from "./demoScenarios.js";

const profile = {
  average_amount: 1200,
  known_devices: ["trusted-phone"],
  familiar_locations: ["Kolkata"],
};
const history = [{ recipient_upi_id: "friend@demo", status: "ALLOW" }];

test("everyday scenario uses real demo behavior and never manufactures coordinates", () => {
  const scenario = buildDemoScenario("everyday", profile, history, "2026-11-16T12:00");
  assert.equal(scenario.amount, "1200");
  assert.equal(scenario.device, "trusted-phone");
  assert.equal(scenario.location_name, "Kolkata");
  assert.equal(scenario.recipient_upi_id, "friend@demo");
  assert.equal(scenario.latitude, "");
  assert.equal(scenario.longitude, "");
  assert.equal(scenario.risk_score, undefined);
});

test("new device and high-value scenarios change submitted input only", () => {
  const newDevice = buildDemoScenario("new-device", profile, history);
  assert.equal(newDevice.amount, "1200");
  assert.equal(newDevice.device, "SIM-UNRECOGNIZED-DEVICE");
  const high = buildDemoScenario("high-value", profile, history);
  assert.equal(high.amount, "50000");
  assert.equal(high.recipient_upi_id, "first-time-recipient@demo");
  assert.equal(high.decision, undefined);
});

test("blocked payments never become the sample known recipient", () => {
  const scenario = buildDemoScenario("everyday", {}, [
    { recipient_upi_id: "attacker@demo", status: "BLOCK" },
  ]);
  assert.equal(scenario.recipient_upi_id, "sample-recipient@demo");
});
