# Plan: Analysis Determinism & Extraction Stability

The objective is to eliminate the fluctuation in requirement counts (e.g., 94 vs 104 vs 77) for the same file by hardening the extraction pipeline against LLM non-determinism and boundary-context loss.

## User Review Required

> [!IMPORTANT]
> This change will involve increasing the context window per page. While this improves accuracy, it slightly increases token usage.

## Proposed Changes

### 🔧 [Extraction Engine](file:///c:/Users/shubh/OneDrive/Documents/AI%20tool/ai-compliance-architect/src/core/extraction.py)

#### [MODIFY] [extraction.py](file:///c:/Users/shubh/OneDrive/Documents/AI%20tool/ai-compliance-architect/src/core/extraction.py)
*   **Increase Overlap**: Increase `self.overlap_buffer` from 500 chars to **1000** chars. This ensures requirements spanning across pages aren't treated as two different pieces by the AI.
*   **Pin Top_P**: Add `"top_p": 0.0001` or the lowest allowed value to the payload to force the LLM to stay on the most "boring" (accurate) token path, reducing the creative randomness that causes different counts.
*   **Text Normalization**: I will add a `normalize_text()` call during the hashing process. This means ` "Shall provide maintenance"` and `"Shall provide  maintenance"` (with an extra space) will be treated as the SAME requirement, preventing duplicates in the final count.

### 🗄️ [Database Handler](file:///c:/Users/shubh/OneDrive/Documents/AI%20tool/ai-compliance-architect/src/utils/db_handler.py)

#### [MODIFY] [db_handler.py](file:///c:/Users/shubh/OneDrive/Documents/AI%20tool/ai-compliance-architect/src/utils/db_handler.py)
*   Ensure that the `req_hash` check is strictly enforced in the `add_requirement` method.

## Verification Plan

### Manual Verification
*   Ask the user to re-upload the AC tender and verify if the result count stays identical across two consecutive runs.
