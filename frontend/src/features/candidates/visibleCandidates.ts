import type { Candidate } from '../../types/api'

export function visibleCandidates(items: Candidate[], rankedIds: string[] | null): Candidate[] {
  const eligible = items.filter((item) => item.status !== 'REJECTED')
  if (rankedIds) {
    const byId = new Map(eligible.map((item) => [item.id, item]))
    return rankedIds.map((id) => byId.get(id)).filter((item): item is Candidate => Boolean(item))
  }
  return [...eligible].sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity))
}
