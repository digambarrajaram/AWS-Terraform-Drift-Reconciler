import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { env } from 'node:process';

export async function emitDriftAlert(name, details, severity = 'high') {
  const summary = `[drift-control] ${name}`;
  const payload = {
    summary,
    severity,
    source: 'aws-terraform-drift-reconciler',
    details,
  };

  const pagerDutyRoutingKey = env.PAGERDUTY_ROUTING_KEY || '';
  const webhookUrl = env.DRIFT_ALERT_WEBHOOK_URL || '';

  if (pagerDutyRoutingKey) {
    try {
      const response = await fetch('https://events.pagerduty.com/v2/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          routing_key: pagerDutyRoutingKey,
          event_action: 'trigger',
          payload: {
            summary,
            severity: severity === 'critical' ? 'critical' : severity === 'medium' ? 'warning' : 'error',
            source: 'aws-terraform-drift-reconciler',
            custom_details: details,
          },
        }),
      });
      if (!response.ok) {
        console.warn(`[drift-alert] PagerDuty alert failed: ${response.status} ${response.statusText}`);
      } else {
        console.log('[drift-alert] PagerDuty alert sent');
      }
    } catch (error) {
      console.warn(`[drift-alert] PagerDuty alert failed: ${error.message}`);
    }
  }

  if (webhookUrl) {
    try {
      const response = await fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        console.warn(`[drift-alert] Webhook alert failed: ${response.status} ${response.statusText}`);
      } else {
        console.log('[drift-alert] Webhook alert sent');
      }
    } catch (error) {
      console.warn(`[drift-alert] Webhook alert failed: ${error.message}`);
    }
  }

  if (!pagerDutyRoutingKey && !webhookUrl) {
    console.log(`[drift-alert] ${summary}`);
  }
}

export function sha256(content) {
  return createHash('sha256').update(content).digest('hex');
}

export function loadJson(filePath) {
  const content = readFileSync(filePath, 'utf8');
  return JSON.parse(content);
}

export function readText(filePath) {
  return readFileSync(filePath, 'utf8');
}
