const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
const ADMIN_API_KEY = import.meta.env.VITE_ADMIN_API_KEY

export type SystemStatus = {
  business: { id: string; name: string; industry: string; default_language: string }
  bot: { globally_paused: boolean; llm_provider: string; llm_model: string; app_env: string }
  metrics_summary: { open_escalations: number; total_contacts: number; total_suppressed: number }
}

export type DeliveryStats = Record<string, number>
export type Escalation = {
  id: number
  conversation_id: number
  contact_id: number
  reason: string
  status: string
  summary: string | null
  detected_intent: string | null
  confidence: number | null
  assigned_to: string | null
  created_at: string
}
export type TemplateRecord = {
  id: number
  name: string
  language: string
  category: string
  status: string
  body: string
  purpose: string | null
  requires_opt_in: boolean
  variables: Record<string, unknown>[]
  created_at: string
  updated_at: string
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers)
  headers.set('Accept', 'application/json')
  if (ADMIN_API_KEY) headers.set('X-Admin-API-Key', ADMIN_API_KEY)
  if (options?.body) headers.set('Content-Type', 'application/json')
  const response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers })
  if (!response.ok) throw new Error(`API request failed (${response.status})`)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  getStatus: () => request<SystemStatus>('/admin/status'),
  getDeliveryStats: () => request<DeliveryStats>('/admin/stats/delivery'),
  getEscalations: () => request<Escalation[]>('/admin/escalations'),
  getTemplates: () => request<TemplateRecord[]>('/admin/templates'),
  createTemplate: (template: Omit<TemplateRecord, 'id' | 'created_at' | 'updated_at'>) => request<TemplateRecord>('/admin/templates', { method: 'POST', body: JSON.stringify(template) }),
  updateTemplate: (id: number, template: Omit<TemplateRecord, 'id' | 'created_at' | 'updated_at'>) => request<TemplateRecord>(`/admin/templates/${id}`, { method: 'PUT', body: JSON.stringify(template) }),
  deleteTemplate: (id: number) => request<void>(`/admin/templates/${id}`, { method: 'DELETE' }),
  acknowledgeEscalation: (id: number) => request(`/admin/escalations/${id}/acknowledge`, { method: 'POST', body: JSON.stringify({ actor: 'operator' }) }),
  resolveEscalation: (id: number, note?: string) => request(`/admin/escalations/${id}/resolve`, { method: 'POST', body: JSON.stringify({ actor: 'operator', note }) }),
  pauseBot: () => request('/admin/bot/pause', { method: 'POST', body: JSON.stringify({ actor: 'operator', reason: 'operator_manual_pause' }) }),
  resumeBot: () => request('/admin/bot/resume', { method: 'POST', body: JSON.stringify({ actor: 'operator' }) }),
}
