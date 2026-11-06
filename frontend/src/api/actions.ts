import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { $api } from "./client";

/**
 * One idempotency key per intent. A dialog keeps its key while it is open, so pressing the
 * button again after a lost response retries the same request instead of creating a second
 * run. `renew` starts a new intent.
 */
export function useIdempotencyKey(): [string, () => void] {
  const [key, setKey] = useState(() => crypto.randomUUID());
  return [key, () => setKey(crypto.randomUUID())];
}

/** After any write, every list and the run itself are read again. */
function useInvalidate() {
  const client = useQueryClient();
  return () => client.invalidateQueries();
}

export function useCreateRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs", { onSuccess: invalidate });
}

export function useVerifyRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs/{run_id}/verify", { onSuccess: invalidate });
}

export function useApplyRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs/{run_id}/apply", { onSuccess: invalidate });
}

export function useCancelRun() {
  const invalidate = useInvalidate();
  return $api.useMutation("post", "/runs/{run_id}/cancel", { onSuccess: invalidate });
}
