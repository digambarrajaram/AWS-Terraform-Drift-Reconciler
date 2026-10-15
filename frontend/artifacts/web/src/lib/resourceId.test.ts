import { describe, expect, it } from 'vitest';
import { shortenResourceId } from './resourceId';

describe('shortenResourceId', () => {
  it('keeps short ids unchanged', () => {
    expect(shortenResourceId('aws_s3_bucket.logs')).toBe('aws_s3_bucket.logs');
  });

  it('prefers the resource leaf so siblings stay distinct', () => {
    const a = 'module.api.aws_apigatewayv2_route.this["GET /objects/{key}"]';
    const b = 'module.api.aws_apigatewayv2_route.this["PUT /objects/{key}"]';
    const sa = shortenResourceId(a, 36);
    const sb = shortenResourceId(b, 36);
    expect(sa).not.toBe(sb);
    expect(sa).toContain('GET');
    expect(sb).toContain('PUT');
  });

  it('end-truncates very long leaves', () => {
    const id = `aws_thing.${'x'.repeat(80)}`;
    const short = shortenResourceId(id, 20);
    expect(short.length).toBeLessThanOrEqual(20);
    expect(short.startsWith('…')).toBe(true);
  });
});
