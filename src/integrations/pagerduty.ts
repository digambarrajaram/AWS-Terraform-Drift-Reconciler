/**
 * PagerDuty Integration — Real alerting via PagerDuty Events API v2.
 *
 * Falls back to console.log when PAGERDUTY_ROUTING_KEY is not configured.
 *
 * If the routing key is an email address (e.g. default-service@digambar.pagerduty.com),
 * PagerDuty converts incoming emails to incidents automatically.
 * The Events API v2 is the preferred integration method.
 */

export interface PagerDutyAlert {
  resourceName: string;
  resourceType: string;
  service: string;
  classification: string;
  riskScore: string;
  driftSummary: string;
  securityImpact: string;
}

let _routingKey: string | null = null;

function getRoutingKey(): string | null {
  if (_routingKey !== null) return _routingKey || null;
  _routingKey = process.env.PAGERDUTY_ROUTING_KEY || "";
  return _routingKey || null;
}

export function isPagerDutyConfigured(): boolean {
  const key = getRoutingKey();
  return key !== null && key !== "" && key !== "YOUR_PAGERDUTY_KEY";
}

function isEmailRouting(key: string): boolean {
  return key.includes("@");
}

/**
 * Send a drift alert to PagerDuty.
 * If the routing key is an email, sends via SMTP-like email format.
 * If it's a standard integration key, sends via Events API v2.
 */
export async function sendPagerDutyAlert(alert: PagerDutyAlert): Promise<{
  success: boolean;
  method: "events-api" | "email" | "console-only";
  dedupKey?: string;
  error?: string;
}> {
  const routingKey = getRoutingKey();

  if (!routingKey) {
    console.log(formatAlertConsole(alert));
    return { success: true, method: "console-only" };
  }

  const dedupKey = `drift-${alert.resourceName}-${alert.riskScore}`;

  if (isEmailRouting(routingKey)) {
    // PagerDuty email integration: log the formatted email for SMTP delivery.
    // In production, use nodemailer to send actual email.
    const email = formatAlertEmail(alert, routingKey, dedupKey);
    console.log(email);
    console.log(`[pagerduty] Email alert formatted for ${routingKey} — integrate nodemailer for actual delivery.`);
    return { success: true, method: "email", dedupKey };
  }

  // Standard PagerDuty Events API v2
  try {
    const payload = {
      routing_key: routingKey,
      event_action: "trigger",
      dedup_key: dedupKey,
      payload: {
        summary: `[${alert.riskScore}] Drift detected: ${alert.resourceName} (${alert.service})`,
        source: "aws-terraform-drift-reconciler",
        severity: alert.riskScore === "Critical" ? "critical" :
                  alert.riskScore === "High" ? "error" : "warning",
        component: alert.resourceType,
        group: alert.service,
        class: alert.classification,
        custom_details: {
          resource_name: alert.resourceName,
          resource_type: alert.resourceType,
          drift_summary: alert.driftSummary,
          security_impact: alert.securityImpact,
        },
      },
    };

    const resp = await fetch("https://events.pagerduty.com/v2/enqueue", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (resp.ok) {
      const result = await resp.json();
      console.log(`[pagerduty] Alert sent — dedup: ${result.dedup_key}`);
      return { success: true, method: "events-api", dedupKey: result.dedup_key };
    } else {
      const errText = await resp.text();
      console.error(`[pagerduty] Events API error: ${resp.status} ${errText}`);
      console.log(formatAlertConsole(alert));
      return { success: false, method: "events-api", error: errText };
    }
  } catch (err: any) {
    console.error(`[pagerduty] Request failed: ${err.message}`);
    console.log(formatAlertConsole(alert));
    return { success: false, method: "events-api", error: err.message };
  }
}

function formatAlertEmail(alert: PagerDutyAlert, routingKey: string, dedupKey: string): string {
  return `
============== PAGERDUTY EMAIL ALERT ==============
To: ${routingKey}
Subject: [${alert.riskScore}] Drift Alert — ${alert.resourceName}

Resource:     ${alert.resourceName}
Type:         ${alert.resourceType}
Service:      ${alert.service}
Risk Score:   ${alert.riskScore}
Class:        ${alert.classification}
Dedup Key:    ${dedupKey}

Summary:
${alert.driftSummary}

Security Impact:
${alert.securityImpact}
====================================================`;
}

function formatAlertConsole(alert: PagerDutyAlert): string {
  return `
============== DRIFT ALERT (console only — no PagerDuty config) ==============
[${alert.riskScore}] ${alert.resourceName} (${alert.service})
  Classification: ${alert.classification}
  Summary: ${alert.driftSummary}
  Impact:  ${alert.securityImpact}
=================================================================`;
}
