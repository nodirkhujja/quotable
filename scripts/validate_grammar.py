"""Validate grammar assessment data — check build words match answers."""

import json
import sys

with open("scripts/grammar_data.json") as f:
    data = json.load(f)

errors = []
for unit_key, questions in data.items():
    for i, q in enumerate(questions):
        if q["type"] == "build":
            # Check: joining words in answer order should reconstruct the answer
            answer = q["answer"]
            words_set = sorted(q["words"])
            answer_words = answer.split()
            answer_sorted = sorted(answer_words)

            if words_set != answer_sorted:
                errors.append(f"{unit_key} build #{i}: words {q['words']} don't match answer '{answer}'")
                # Show diff
                extra_in_words = set(q["words"]) - set(answer_words)
                extra_in_answer = set(answer_words) - set(q["words"])
                if extra_in_words:
                    errors[-1] += f"\n  Extra in words: {extra_in_words}"
                if extra_in_answer:
                    errors[-1] += f"\n  Extra in answer: {extra_in_answer}"

        elif q["type"] == "mcq":
            # Check answer index is valid
            if q["answer"] < 0 or q["answer"] >= len(q["options"]):
                errors.append(f"{unit_key} mcq #{i}: answer index {q['answer']} out of range")

if errors:
    print(f"Found {len(errors)} errors:\n")
    for e in errors:
        print(f"  ❌ {e}\n")
else:
    print("✅ All build words match answers. Data is clean!")

# Stats
total = sum(len(qs) for qs in data.values())
mcq = sum(1 for qs in data.values() for q in qs if q["type"] == "mcq")
build = sum(1 for qs in data.values() for q in qs if q["type"] == "build")
print(f"\nTotal: {total} questions ({mcq} MCQ, {build} Build) across {len(data)} units")
