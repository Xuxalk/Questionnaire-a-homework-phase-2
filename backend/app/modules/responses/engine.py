import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


class InvalidAnswer(Exception):
    """The answer does not match its question definition."""


class InvalidStoredSteps(Exception):
    """A persisted response contains an invalid or duplicate step."""


@dataclass(frozen=True)
class PathState:
    path_question_ids: List[str]
    next_question_id: Optional[str]
    complete: bool


def validate_answer(question: Dict[str, Any], value: Any) -> Dict[str, Any]:
    """Validate and normalize one answer against the published question."""
    if not isinstance(value, dict):
        raise InvalidAnswer("Answer must be an object")

    question_type = question["type"]
    options = question.get("options", [])
    option_ids = {option["option_id"] for option in options}
    validation = question.get("validation", {})

    if question_type == "single_choice":
        if set(value) != {"option_id"}:
            raise InvalidAnswer("Single-choice answer must contain option_id")
        option_id = value["option_id"]
        if not isinstance(option_id, str) or option_id not in option_ids:
            raise InvalidAnswer("Selected option does not belong to this question")
        return {"option_id": option_id}

    if question_type == "multi_choice":
        if set(value) != {"option_ids"} or not isinstance(value["option_ids"], list):
            raise InvalidAnswer("Multi-choice answer must contain option_ids")
        selected = value["option_ids"]
        if any(not isinstance(option_id, str) for option_id in selected):
            raise InvalidAnswer("Selected option IDs must be strings")
        if len(selected) != len(set(selected)):
            raise InvalidAnswer("An option cannot be selected more than once")
        if any(option_id not in option_ids for option_id in selected):
            raise InvalidAnswer("Selected option does not belong to this question")
        minimum = validation.get("min_selected", 0)
        maximum = validation.get("max_selected", len(option_ids))
        if not minimum <= len(selected) <= maximum:
            raise InvalidAnswer(
                "Select between {} and {} options".format(minimum, maximum)
            )
        return {"option_ids": list(selected)}

    if question_type == "text":
        if set(value) != {"text"} or not isinstance(value["text"], str):
            raise InvalidAnswer("Text answer must contain a string text field")
        text = value["text"]
        minimum = validation.get("min_length")
        maximum = validation.get("max_length")
        if minimum is not None and len(text) < minimum:
            raise InvalidAnswer("Text is shorter than min_length")
        if maximum is not None and len(text) > maximum:
            raise InvalidAnswer("Text is longer than max_length")
        return {"text": text}

    if question_type == "number":
        if set(value) != {"number"}:
            raise InvalidAnswer("Number answer must contain number")
        number = value["number"]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise InvalidAnswer("Number answer must be numeric")
        try:
            finite = math.isfinite(float(number))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise InvalidAnswer("Number answer must be finite")
        minimum = validation.get("min_value")
        maximum = validation.get("max_value")
        if minimum is not None and number < minimum:
            raise InvalidAnswer("Number is below min_value")
        if maximum is not None and number > maximum:
            raise InvalidAnswer("Number is above max_value")
        if validation.get("integer_only", False) and not float(number).is_integer():
            raise InvalidAnswer("Number must be an integer")
        return {"number": number}

    raise InvalidAnswer("Unsupported question type")


def evaluate_path(
    survey: Dict[str, Any], steps: Sequence[Dict[str, Any]]
) -> PathState:
    """Follow saved answers from the first question to the next incomplete step."""
    questions = survey.get("questions", [])
    question_indexes = {
        question["question_id"]: index for index, question in enumerate(questions)
    }
    step_by_question: Dict[str, Dict[str, Any]] = {}
    for step in steps:
        question_id = step.get("question_id")
        if question_id in step_by_question:
            raise InvalidStoredSteps("A response has duplicate question steps")
        if question_id not in question_indexes:
            raise InvalidStoredSteps("A response step references a missing question")
        step_by_question[question_id] = step

    path: List[str] = []
    index = 0
    visited = set()
    while index < len(questions):
        question = questions[index]
        question_id = question["question_id"]
        if question_id in visited:
            raise InvalidStoredSteps("Question path contains a cycle")
        visited.add(question_id)
        path.append(question_id)

        step = step_by_question.get(question_id)
        if step is None:
            return PathState(path, question_id, False)
        if step.get("status") == "skipped":
            if question.get("required"):
                raise InvalidStoredSteps("A required question was marked skipped")
            index += 1
            continue
        if step.get("status") != "answered":
            raise InvalidStoredSteps("Unknown response step status")

        target = _matched_target(question, step.get("value"))
        if target is None:
            index += 1
        else:
            target_index = question_indexes.get(target)
            if target_index is None or target_index <= index:
                raise InvalidStoredSteps("A jump target is not a later question")
            index = target_index

    return PathState(path, None, True)


def _matched_target(question: Dict[str, Any], value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        raise InvalidStoredSteps("Answered step has no answer object")
    question_type = question.get("type")
    for rule in sorted(question.get("jump_rules", []), key=lambda item: item["priority"]):
        condition = rule["condition"]
        if question_type == "single_choice":
            matches = (
                condition.get("operator") == "equals"
                and value.get("option_id") == condition.get("option_id")
            )
        elif question_type == "multi_choice":
            selected = set(value.get("option_ids", []))
            referenced = set(condition.get("option_ids", []))
            if condition.get("match") == "ANY":
                matches = bool(selected.intersection(referenced))
            elif condition.get("match") == "ALL":
                matches = referenced.issubset(selected)
            else:
                matches = False
        elif question_type == "number":
            number = value.get("number")
            target_number = condition.get("value")
            operator = condition.get("operator")
            matches = _compare_numbers(number, operator, target_number)
        else:
            matches = False
        if matches:
            return rule["target_question_id"]
    return None


def _compare_numbers(number: Any, operator: Any, target: Any) -> bool:
    if (
        isinstance(number, bool)
        or not isinstance(number, (int, float))
        or isinstance(target, bool)
        or not isinstance(target, (int, float))
    ):
        return False
    comparisons = {
        "eq": lambda: number == target,
        "ne": lambda: number != target,
        "gt": lambda: number > target,
        "gte": lambda: number >= target,
        "lt": lambda: number < target,
        "lte": lambda: number <= target,
    }
    comparison = comparisons.get(operator)
    return bool(comparison and comparison())
