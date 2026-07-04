#!/usr/bin/env python3
"""Configuration drift agent for managed AWS instances.

This agent receives a baseline and a list of managed instances via stdin.
It checks whether instance platform/version/association state differs from baseline.
"""

import json
import sys


def main():
    try:
        payload = json.load(sys.stdin)
        managed_instances = payload.get('managedInstances', [])
        baseline = payload.get('baseline', {})

        mismatches = []
        for instance in managed_instances:
            details = []
            if baseline.get('platformName') and instance.get('platformName') != baseline.get('platformName'):
                details.append(f"platformName expected={baseline.get('platformName')} actual={instance.get('platformName')}")
            if baseline.get('platformVersion') and instance.get('platformVersion') != baseline.get('platformVersion'):
                details.append(f"platformVersion expected={baseline.get('platformVersion')} actual={instance.get('platformVersion')}")
            if baseline.get('associationStatus') and instance.get('associationStatus') != baseline.get('associationStatus'):
                details.append(f"associationStatus expected={baseline.get('associationStatus')} actual={instance.get('associationStatus')}")
            if details:
                mismatches.append({
                    'instanceId': instance.get('instanceId'),
                    'details': '; '.join(details),
                })

        classification = 'moderate_risk_change' if mismatches else 'low_risk_change'
        risk_score = 'Medium' if mismatches else 'Low'
        explanation = (
            f"Configuration drift found on {len(mismatches)} managed instance(s)."
            if mismatches else
            'Managed instance configuration matches the expected baseline.'
        )
        security_impact = (
            'Undocumented OS/package/config drift increases attack surface.'
            if mismatches else
            'Managed configuration is consistent with baseline.'
        )
        output = {
            'resourceId': payload.get('resourceId', 'config_drift'),
            'category': 'configuration',
            'classification': classification,
            'riskScore': risk_score,
            'explanation': explanation,
            'securityImpact': security_impact,
            'costImpact': 'Review drift to avoid configuration remediation costs.',
            'hclFix': '',
            'fixType': 'configuration_review',
            'correctionAttempts': 1,
            'configDetails': {
                'missingSettings': [],
                'mismatchedSettings': {},
                'summary': '; '.join([m['details'] for m in mismatches]),
            },
            'validationStatus': 'passed',
        }
        print(json.dumps(output))
    except Exception as e:
        print(json.dumps({'error': str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
