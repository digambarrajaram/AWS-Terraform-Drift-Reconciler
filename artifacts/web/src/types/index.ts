/** A deployment environment returned by GET /api/environments */
export interface Environment {
  slug: string;
  name: string;
  is_active: boolean;
  [key: string]: unknown;
}
