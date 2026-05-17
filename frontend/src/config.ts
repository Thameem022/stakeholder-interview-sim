const rawApiBaseUrl = import.meta.env.VITE_API_URL?.trim() || ''

export const API_BASE_URL = rawApiBaseUrl.replace(/\/$/, '')
