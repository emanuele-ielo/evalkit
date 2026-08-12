import { useCallback, useEffect, useRef, useState } from 'react'
import type { AttemptDetail, CampaignDetail, CampaignSummary, ProgressEvent } from './types'

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`${response.status} ${response.statusText}: ${detail.slice(0, 200)}`)
  }
  return (await response.json()) as T
}

export const api = {
  campaigns: () => getJSON<CampaignSummary[]>('/api/campaigns'),
  campaign: (id: string, refresh = false) =>
    getJSON<CampaignDetail>(`/api/campaigns/${encodeURIComponent(id)}${refresh ? '?refresh=1' : ''}`),
  attempt: (id: string, scenario: string, round: number) =>
    getJSON<AttemptDetail>(
      `/api/campaigns/${encodeURIComponent(id)}/attempts/${encodeURIComponent(scenario)}/${round}`,
    ),
  officialDiff: (id: string) => getJSON<{
    counts: Record<string, number>
    attempts: number
    agreement_rate: number | null
    disagreements: { scenario: string; short: string; round: number; agreement: string; ours: string; official: string; taxonomy: string[] }[]
  }>(`/api/campaigns/${encodeURIComponent(id)}/official-diff`),
  compare: (base: string, other: string) =>
    getJSON<Record<string, unknown>>(`/api/compare?base=${encodeURIComponent(base)}&other=${encodeURIComponent(other)}`),
  rawUrl: (id: string, scenario: string, round: number, kind: string) =>
    `/api/campaigns/${encodeURIComponent(id)}/attempts/${encodeURIComponent(scenario)}/${round}/raw/${kind}`,
}

/** Load once, with loading/error state and a manual refresh. */
export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
} {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  useEffect(() => {
    let alive = true
    setLoading(true)
    loaderRef
      .current()
      .then((value) => {
        if (alive) {
          setData(value)
          setError(null)
        }
      })
      .catch((exc: Error) => alive && setError(exc.message))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { data, error, loading, reload: useCallback(() => setNonce((n) => n + 1), []) }
}

/**
 * Subscribe to a campaign's progress stream. Returns the last events and a
 * counter that bumps whenever something lands, so views can refetch cheaply.
 */
export function useCampaignEvents(campaignId: string | null): { events: ProgressEvent[]; tick: number } {
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [tick, setTick] = useState(0)

  useEffect(() => {
    if (!campaignId) return
    const source = new EventSource(`/api/campaigns/${encodeURIComponent(campaignId)}/events`)
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as ProgressEvent
        setEvents((current) => [...current.slice(-199), event])
        if (['attempt_finished', 'attempt_judged', 'campaign_finished', 'judge_finished'].includes(event.type)) {
          setTick((value) => value + 1)
        }
      } catch {
        /* ignore malformed frames */
      }
    }
    source.onerror = () => source.close()
    return () => source.close()
  }, [campaignId])

  return { events, tick }
}
