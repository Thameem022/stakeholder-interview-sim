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

export const healthCheck = async () => {
  const { data } = await apiClient.get('/api/health')
  return data
}

export default apiClient
