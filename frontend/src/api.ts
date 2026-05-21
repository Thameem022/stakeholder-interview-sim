import axios from 'axios'

const apiClient = axios.create({
  baseURL: '',
  timeout: 60000,
})

export interface Persona {
  key: string
  display_name: string
}

export interface Voice {
  persona_id: string
  voice_id: string
}

export const getPersonas = async (): Promise<Persona[]> => {
  const { data } = await apiClient.get('/api/personas')
  return data
}

export const getVoices = async (): Promise<Voice[]> => {
  const { data } = await apiClient.get('/api/voices')
  return data
}

export const evalIqr = async (sessionId: string) => {
  const { data } = await apiClient.post('/api/eval/iqr', null, { params: { session_id: sessionId } })
  return data
}

export const evalSic = async (sessionId: string) => {
  const { data } = await apiClient.post('/api/eval/sic', null, { params: { session_id: sessionId } })
  return data
}

export const getLatestEvaluation = async (sessionId: string) => {
  const { data } = await apiClient.get(`/api/eval/sessions/${sessionId}/latest`)
  return data
}

export const healthCheck = async () => {
  const { data } = await apiClient.get('/api/health')
  return data
}

export interface RealtimeToken {
  ephemeral_key: string
  session_id: string
  model: string
}

export const getRealtimeToken = async (
  personaId: string,
  voiceId?: string,
  sessionId?: string
): Promise<RealtimeToken> => {
  const { data } = await apiClient.post('/api/realtime/token', {
    persona_id: personaId,
    voice_id: voiceId,
    session_id: sessionId,
  })
  return data
}

export const postRetrieve = async (
  personaId: string,
  query: string
): Promise<{ text: string }> => {
  const { data } = await apiClient.post('/api/realtime/retrieve', {
    persona_id: personaId,
    query,
  })
  return data
}

export const postTranscript = async (
  sessionId: string,
  role: 'user' | 'assistant',
  text: string,
  ended?: boolean
): Promise<{ ok: boolean; turns: number }> => {
  const { data } = await apiClient.post('/api/realtime/transcript', {
    session_id: sessionId,
    role,
    text,
    ended,
  })
  return data
}

export default apiClient
