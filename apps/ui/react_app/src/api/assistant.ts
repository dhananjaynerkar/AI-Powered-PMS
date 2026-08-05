import type { DemoAnswer, GroundedAnswer, RetrievalReadiness, StructuredAnswer } from "../types";
import { requestJson } from "./client";

export function runPolicyQuery(
  question: string,
  accessToken: string
): Promise<GroundedAnswer> {
  return requestJson<GroundedAnswer>("/policy/query", accessToken, {
    method: "POST",
    body: JSON.stringify({ question, response_language: "auto" })
  });
}

export function runStructuredQuery(
  question: string,
  accessToken: string
): Promise<StructuredAnswer> {
  return requestJson<StructuredAnswer>("/query", accessToken, {
    method: "POST",
    body: JSON.stringify({ question, limit: 50 })
  });
}

export function runDemoQuery(question: string, limit = 5): Promise<DemoAnswer> {
  return requestJson<DemoAnswer>("/demo/query", "", {
    method: "POST",
    body: JSON.stringify({ question, limit })
  });
}

export function loadRetrievalReadiness(): Promise<RetrievalReadiness> {
  return requestJson<RetrievalReadiness>("/retrieval/readiness", "");
}
