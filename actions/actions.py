from typing import Any, Dict, List, Text
import os
import re
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet, FollowupAction, Restarted
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.forms import FormValidationAction

try:
    import litellm
except ImportError:  # pragma: no cover
    litellm = None

load_dotenv()


def _has_user_text(tracker: Tracker) -> bool:
    text = (tracker.latest_message.get("text") or "").strip()
    return bool(text)




class ActionCheckSufficientFunds(Action):
    def name(self) -> Text:
        return "action_check_sufficient_funds"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        if not _has_user_text(tracker):
            return []
        # hard-coded balance for tutorial purposes. in production this
        # would be retrieved from a database or an API
        balance = 1000
        transfer_amount = tracker.get_slot("amount")
        has_sufficient_funds = transfer_amount <= balance
        return [SlotSet("has_sufficient_funds", has_sufficient_funds)]


class ActionStartReflectFlow(Action):
    def name(self) -> str:
        return "action_start_reflect_flow"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Dispatch the correct reflection utterance based on the mood slot."""
        if not _has_user_text(tracker):
            return []
        # Determine current intent and handle mood selection
        intent = tracker.latest_message.get("intent", {}).get("name")
        mood = tracker.get_slot("mood")
        support_completed = tracker.get_slot("support_completed")

        # Normalize mood to expected keys
        if isinstance(mood, str):
            mood_key = mood.lower()
        else:
            mood_key = None

        # Fallback to intent name if slot missing
        if not mood_key:
            intent_name = intent
            if intent_name == "mood_happy":
                mood_key = "happy"
            elif intent_name == "mood_sad":
                mood_key = "sad"
            elif intent_name == "mood_angry":
                mood_key = "angry"

        # Map mood to utterances
        utter_mapping = {
            "happy": "utter_reflect_mood_happy",
            "sad": "utter_reflect_mood_sad",
            "angry": "utter_reflect_mood_angry",
        }

        # If we just completed the support flow, acknowledge the new mood and end.
        if support_completed and mood_key in utter_mapping:
            followup_mapping = {
                "happy": "utter_followup_mood_happy",
                "sad": "utter_followup_mood_sad",
                "angry": "utter_followup_mood_angry",
            }
            utter_name = followup_mapping.get(mood_key)
            if utter_name:
                dispatcher.utter_message(response=utter_name)
                if mood_key in ["sad", "angry"]:
                    dispatcher.utter_message(response="utter_reason_why_you_feel_upset_question")
                return [
                    SlotSet("support_completed", None),
                    SlotSet("mood", mood_key),
                    SlotSet("last_mood", mood_key),
                ]

        utter_name = utter_mapping.get(mood_key)

        if utter_name:
            print(f"[action_start_reflect_flow] mood_key={mood_key}, sending utter={utter_name}")
            # Send the reflection utterance
            dispatcher.utter_message(response=utter_name)
            # (Optional) additional support message could be sent here
            # For sad and angry moods, also send the reason question
            if mood_key in ["sad", "angry"]:
                dispatcher.utter_message(response="utter_reason_why_you_feel_upset_question")
            # Ensure mood slot is normalized and store a preserved copy in last_mood
            return [SlotSet("mood", mood_key), SlotSet("last_mood", mood_key)]

        # If we couldn't resolve the mood, ask for clarification
        print("[action_start_reflect_flow] could not resolve mood")
        dispatcher.utter_message(text="I didn't catch that — can you tell me how you feel?")
        return []


class ActionHandleReasonResponse(Action):
    def name(self) -> str:
        return "action_handle_reason_response"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Handle the user's response to 'Do you know what made you feel upset?'"""
        if not _has_user_text(tracker):
            return []
        intent = tracker.latest_message.get("intent", {}).get("name")
        
        print(f"[action_handle_reason_response] intent={intent}")
        
        # Only handle deny/affirm intents. For other intents, silently return
        # so the flow can continue or switch to another flow/pattern
        if intent == "deny":
            # User doesn't know the reason
            dispatcher.utter_message(response="utter_acknowledge_uneasy_feeling")
            # Also provide an overview of common reasons in a separate message
            mood = tracker.get_slot("mood")
            if mood == "sad":
                dispatcher.utter_message(response="utter_overview_common_reasons_sad")
            elif mood == "angry":
                dispatcher.utter_message(response="utter_overview_common_reasons_angry")
            else:
                # Fallback to the generic overview if mood is unknown
                dispatcher.utter_message(response="utter_overview_common_reasons_sad")
            return [SlotSet("expect_free_reason", None)]
        elif intent == "affirm":
            # User knows the reason: create space for them to explain it
            dispatcher.utter_message(response="utter_ask_reason_after_affirm")
            return [SlotSet("expect_free_reason", True)]
        # For any other intent (mood_happy, mood_sad, mood_angry), do nothing
        # and let the flow handle it
        return []


class ActionHandlePickReason(Action):
    def name(self) -> str:
        return "action_handle_pick_reason"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Handle the user's selection from the list of possible reasons.
        If they still don't know (dont_know), send two follow-up messages.
        Otherwise, acknowledge the selected reason.
        """
        if not _has_user_text(tracker):
            return []
        intent = tracker.latest_message.get("intent", {}).get("name")
        expect_free_reason = tracker.get_slot("expect_free_reason")

        if not expect_free_reason and intent != "pick_reason":
            # Only accept free-text reasons when explicitly prompted after "Yes".
            return []

        reason = tracker.get_slot("reason")
        if not reason:
            entities = tracker.latest_message.get("entities") or []
            for entity in entities:
                if entity.get("entity") == "reason":
                    reason = entity.get("value")
                    break
        if not reason and expect_free_reason:
            text_reason = (tracker.latest_message.get("text") or "").strip()
            if text_reason and intent not in ("affirm", "deny"):
                reason = text_reason
        mood = tracker.get_slot("mood")
        print(f"[action_handle_pick_reason] intent={intent} reason={reason} mood={mood}")

        # If the user selected or wrote "I don't know" - stop support flow
        if reason == "dont_know":
            dispatcher.utter_message(response="utter_reason_unknown_exercise")
            dispatcher.utter_message(response="utter_reason_unknown_ask_later")
            # Do NOT proceed to the support flow automatically for 'dont_know'.
            # Do not clear the 'mood' slot so we retain the user's emotional state
            # for future commands. Only clear the reason and support_stage.
            return [
                SlotSet("reason", None),
                SlotSet("support_stage", None),
                SlotSet("expect_free_reason", None),
            ]

        # Acknowledge other selected reasons - generic response for now
        # Otherwise, if the user picked (or typed) a reason, continue to reframe flow
        if reason:
            friendly_reason = self._normalize_reason(reason)
            # Log and proceed directly to the reframing flow
            log_user_state(tracker.get_slot("mood"), friendly_reason)
            return [
                SlotSet("reason", friendly_reason),
                SlotSet("expect_free_reason", None),
                FollowupAction("action_handle_reframe_flow"),
            ]

        # Otherwise, do nothing
        return []

    @staticmethod
    def _normalize_reason(reason: str) -> str:
        """Turn reason codes into friendlier phrasing for conversation."""
        mapping = {
            "tired": "being tired",
            "missing_someone": "missing someone",
            "change_in_routine": "something changed at home",
            "worry_school": "worrying about school",
            "dont_know": "not sure",
            "frustration": "feeling frustrated",
            "someone_bothered_me": "someone upset you",
            "feeling_ignored": "feeling ignored",
            "overstimulation": "a noisy or overwhelming place",
        }
        return mapping.get(reason, str(reason).replace("_", " "))


class ActionHandleSupportFlow(Action):
    def name(self) -> str:
        return "action_handle_support_flow"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Manage the simple 4-step supportive flow: common_ground, acceptance, analysis, nuance.
           Uses a slot 'support_stage' to track progress. If user answers 'affirm' to continue,
           proceed; if 'deny', stop the flow.
        """
        if not _has_user_text(tracker):
            return []
        intent = tracker.latest_message.get("intent", {}).get("name")
        stage = tracker.get_slot("support_stage")
        mood = tracker.get_slot("mood")
        reason = tracker.get_slot("reason")
        support_completed = tracker.get_slot("support_completed")

        print(f"[action_handle_support_flow] intent={intent} stage={stage} mood={mood} reason={reason}")

        # If a new mood was selected after completion, hand off to reflect flow.
        if support_completed and intent in ("mood_happy", "mood_sad", "mood_angry"):
            mood_map = {
                "mood_happy": "happy",
                "mood_sad": "sad",
                "mood_angry": "angry",
            }
            next_mood = mood_map.get(intent)
            return [
                SlotSet("support_stage", None),
                SlotSet("reason", None),
                SlotSet("support_completed", None),
                SlotSet("mood", next_mood),
                SlotSet("last_mood", next_mood),
                FollowupAction("action_start_reflect_flow"),
            ]

        # If no stage set, start with common ground.
        if not stage:
            if not reason:
                return []
            dispatcher.utter_message(response="utter_stage_common_ground")
            dispatcher.utter_message(response="utter_stage_continue_question")
            return [SlotSet("support_stage", "common_ground")]

        # If we have a stage set but the user hasn't replied yes/no yet (e.g. we were invoked
        # as a followup after a reason selection), send the current stage message and wait
        # for the user's affirmation/denial (intent will not be 'affirm' or 'deny').
        if stage and intent not in ("affirm", "deny"):
            if stage == "common_ground":
                dispatcher.utter_message(response="utter_stage_common_ground")
            elif stage == "acceptance":
                dispatcher.utter_message(response="utter_stage_acceptance")
            elif stage == "analysis":
                dispatcher.utter_message(response="utter_stage_analysis")
            elif stage == "nuance":
                dispatcher.utter_message(response="utter_stage_nuance")
            dispatcher.utter_message(response="utter_stage_continue_question")
            return [SlotSet("support_stage", stage)]

        # If currently in common_ground and user affirmed, go to acceptance
        if stage == "common_ground":
            if intent == "affirm":
                dispatcher.utter_message(response="utter_stage_acceptance")
                dispatcher.utter_message(response="utter_stage_continue_question")
                return [SlotSet("support_stage", "acceptance")]
            elif intent == "deny":
                # User doesn't want to continue
                dispatcher.utter_message(text="No worries — we can pause here. If you want to try later, I’ll be here.")
                # Keep 'mood' so we remember the user's current emotion for later.
                return [SlotSet("support_stage", None), SlotSet("reason", None)]

        if stage == "acceptance":
            if intent == "affirm":
                dispatcher.utter_message(response="utter_stage_analysis")
                dispatcher.utter_message(response="utter_stage_continue_question")
                return [SlotSet("support_stage", "analysis")]
            elif intent == "deny":
                dispatcher.utter_message(text="That's okay. We can pause anytime. If you want to continue later, tell me and we can pick it up.")
                # Keep 'mood' so we remember the user's current emotion for later.
                return [SlotSet("support_stage", None), SlotSet("reason", None)]

        if stage == "analysis":
            if intent == "affirm":
                dispatcher.utter_message(response="utter_stage_nuance")
                dispatcher.utter_message(response="utter_stage_continue_question")
                return [SlotSet("support_stage", "nuance")]
            elif intent == "deny":
                dispatcher.utter_message(text="Totally fine — we can stop here for now.")
                # Keep 'mood' so we remember the user's current emotion for later.
                return [SlotSet("support_stage", None), SlotSet("reason", None)]

        if stage == "nuance":
            if intent == "affirm":
                # Final step done, then check if the mood has shifted.
                dispatcher.utter_message(response="utter_support_done_check_mood")
                return [
                    SlotSet("support_stage", None),
                    SlotSet("reason", None),
                    SlotSet("support_completed", True),
                ]
            elif intent == "deny":
                dispatcher.utter_message(text="That’s okay — if you want to keep exploring another time, I’ll be right here.")
                # Keep 'mood' so we remember the user's current emotion for later.
                return [SlotSet("support_stage", None), SlotSet("reason", None), SlotSet("last_mood", None), SlotSet("mood", None)]

        # If none of the above matched, do nothing
        return []

    @staticmethod
    def _suggest_activity(reason: str) -> str:
        """Provide a short, concrete next step tied to the user's reason."""
        r_lower = (reason or "").lower()
        if "miss" in r_lower:
            return (
                "Since you're missing someone, try this: place a photo or memory item nearby, take five 4-6 breaths (inhale 4, exhale 6), "
                "send them a short note or voice message, and plan one small check-in time so you feel connected."
            )
        if "tired" in r_lower or "sleep" in r_lower:
            return "Your body might need a reset: roll your shoulders, take five slow belly breaths, and stretch your neck gently side to side."
        if "school" in r_lower:
            return "Worried about school? Jot one small task you can finish today, then take a 3–3–3 breath (inhale 3, hold 3, exhale 3) before starting."
        if "home" in r_lower or "routine" in r_lower or "change" in r_lower:
            return "When things change at home, anchor yourself: press your feet into the floor, breathe in for 4 and out for 6, and name one thing that still feels steady."
        if "angry" in r_lower or "frustrat" in r_lower:
            return "For the anger: squeeze your fists, release, then try box breathing (4 in, 4 hold, 4 out, 4 hold) for three rounds."
        return "Let's ground together: place a hand on your belly, take five slow breaths, and notice one thing you can see, hear, and feel right now."


class ActionHandleReframeFlow(Action):
    def name(self) -> str:
        return "action_handle_reframe_flow"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Reframe immediately: reflect + alternative suggestion, then offer to continue."""
        intent = tracker.latest_message.get("intent", {}).get("name")
        stage = tracker.get_slot("reframe_stage")
        mood = tracker.get_slot("mood") or "this feeling"
        reason = tracker.get_slot("reason") or "this situation"
        user_text = (tracker.latest_message.get("text") or "").strip()
        detail_slot = tracker.get_slot("reason_detail")

        print(f"[action_handle_reframe_flow] intent={intent} stage={stage} mood={mood} reason={reason} detail={detail_slot}")

        # Step 1: immediate reframe (no consent question)
        if not stage:
            detail = self._clean_detail(user_text, detail_slot, reason)
            reframe_text = self._generate_reframe_text(reason, detail)
            dispatcher.utter_message(text=reframe_text)
            dispatcher.utter_message(response="utter_stage_continue_question")
            return [
                SlotSet("reframe_stage", "wrap"),
                SlotSet("reason_detail", detail),
            ]

        # Step 2: reflect and reframe
        if stage == "reframe":
            detail = self._clean_detail(user_text, detail_slot, reason)
            reframe_text = self._generate_reframe_text(reason, detail)
            dispatcher.utter_message(text=reframe_text)
            dispatcher.utter_message(response="utter_stage_continue_question")
            return [SlotSet("reframe_stage", "wrap"), SlotSet("reason_detail", detail)]

        # Step 3: wrap up or loop if user adds more detail
        if stage == "wrap":
            if intent == "affirm":
                dispatcher.utter_message(response="utter_support_done")
                return [SlotSet("reframe_stage", None), SlotSet("reason_detail", None)]
            if intent == "deny":
                detail = self._clean_detail(user_text, detail_slot, reason)
                alt_text = self._generate_reframe_text(reason, detail)
                dispatcher.utter_message(text="Let's try another angle.")
                dispatcher.utter_message(text=alt_text)
                dispatcher.utter_message(response="utter_stage_continue_question")
                return [
                    SlotSet("reframe_stage", "wrap"),
                    SlotSet("reason_detail", detail),
                ]
            if user_text:
                # New detail—loop back into reframe
                cleaned = self._clean_detail(user_text, detail_slot, reason)
                return [
                    SlotSet("reason_detail", cleaned),
                    SlotSet("reframe_stage", "reframe"),
                    FollowupAction("action_handle_reframe_flow"),
                ]
            dispatcher.utter_message(response="utter_stage_continue_question")
            return []

        return []

    @staticmethod
    def _clean_detail(user_text: str, detail_slot: Any, reason: str) -> str:
        """Clean incoming detail to avoid showing raw command payloads."""
        candidate = detail_slot or user_text or reason or ""
        text = str(candidate).strip()
        if text.startswith("/"):
            return reason.replace("_", " ")
        return text.replace("_", " ")

    @staticmethod
    def _generate_reframe_text(reason: str, detail: str) -> str:
        """Generate a dynamic reframe with LLM; fall back to static suggestion."""
        fallback = ActionHandleSupportFlow._suggest_activity(reason)
        if not litellm:
            return fallback
        prompt = (
            "You are a brief, supportive coach. Reframe the situation to reduce distress "
            "and suggest one concrete, calming next step. Keep it to 2 short sentences. "
            f"Reason: {reason}. Detail: {detail or reason}."
        )
        try:
            resp = litellm.completion(
                model="gemini/gemini-pro",
                messages=[{"role": "user", "content": prompt}],
                api_key=os.getenv("GEMINI_API_KEY"),
                timeout=10,
            )
            text = resp.choices[0].message["content"]
            return text.strip() if text else fallback
        except Exception as e:  # pragma: no cover
            print(f"[action_handle_reframe_flow] llm fallback due to error: {e}")
            return fallback


def log_user_state(mood: Any, reason: Any) -> None:
    """Append mood/reason selections to a local text log for simple traceability."""
    try:
        os.makedirs("logs", exist_ok=True)
        ts = datetime.utcnow().isoformat()
        with open("logs/user_state.log", "a", encoding="utf-8") as f:
            f.write(f"{ts}\tmood={mood or ''}\treason={reason or ''}\n")
    except Exception as e:
        print(f"[log_user_state] failed to write log: {e}")


class ActionGetStoredMood(Action):
    def name(self) -> str:
        return "action_get_stored_mood"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not _has_user_text(tracker):
            return []
        last_mood = tracker.get_slot("last_mood")
        if last_mood:
            dispatcher.utter_message(text=f"I have stored that you felt {last_mood}. If you'd like, we can explore that more.")
        else:
            dispatcher.utter_message(text="I don't have a record of how you were feeling yet. Would you like to tell me?")
        return []
    
class ActionRestartConversation(Action):
    def name(self) -> str:
        return "action_restart_conversation"

    async def run(self, dispatcher, tracker, domain):
        if not _has_user_text(tracker):
            return []
        dispatcher.utter_message(response="utter_restart_ok")

        # Restart clears the conversation state (including slots) back to the start.
        # Then we immediately start your reflect/mood flow again.
        return [
            Restarted(),
            FollowupAction("action_start_reflect_flow"),
        ]


def _normalize_riddle_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _tokenize_riddle_text(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"[a-z0-9]+", text.lower())


class ActionFetchRiddle(Action):
    def name(self) -> Text:
        return "action_fetch_riddle"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        api_key = os.getenv("API_NINJAS_KEY")
        if not api_key:
            dispatcher.utter_message(text="Riddle API key is missing on the server.")
            return []

        try:
            response = requests.get(
                "https://api.api-ninjas.com/v1/riddles",
                headers={"X-Api-Key": api_key},
                timeout=8,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            dispatcher.utter_message(
                text="I couldn't reach the riddle service right now. Try again later."
            )
            return []

        if not data or not isinstance(data, list):
            dispatcher.utter_message(
                text="I didn't get a valid riddle back. Try again."
            )
            return []

        riddle = data[0] if data else {}
        question = riddle.get("question")
        answer = riddle.get("answer")

        if not question or not answer:
            dispatcher.utter_message(
                text="That riddle response was incomplete. Try again."
            )
            return []

        dispatcher.utter_message(text=f"Certainly! Here's your riddle:\n\n{question}")

        return [
            SlotSet("riddle_question", question),
            SlotSet("riddle_answer", answer),
            SlotSet("riddle_attempts", 0),
            SlotSet("guess", None),
            SlotSet("riddle_trigger_text", tracker.latest_message.get("text")),
        ]


class ValidateRiddleForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_riddle_form"

    def extract_guess(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        intent = tracker.latest_message.get("intent", {}).get("name")
        if intent == "play_riddle":
            return {}

        text = (tracker.latest_message.get("text") or "").strip()
        if not text:
            return {}

        if text == (tracker.get_slot("riddle_trigger_text") or "").strip():
            return {}

        return {"guess": text}

    def validate_guess(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        trigger_text = (tracker.get_slot("riddle_trigger_text") or "").strip()
        if not value or not value.strip():
            return {"guess": None}
        if trigger_text and value.strip() == trigger_text:
            return {"guess": None}

        answer = tracker.get_slot("riddle_answer") or ""
        attempts = tracker.get_slot("riddle_attempts") or 0

        attempts = int(attempts) + 1

        user_guess_norm = _normalize_riddle_text(value)
        answer_norm = _normalize_riddle_text(answer)
        user_tokens = _tokenize_riddle_text(value)
        answer_tokens = _tokenize_riddle_text(answer)

        stop_words = {
            "a",
            "an",
            "the",
            "my",
            "your",
            "everyone",
            "everybody",
            "has",
            "have",
            "one",
            "is",
            "are",
            "its",
            "it's",
            "to",
            "of",
        }
        user_core = [token for token in user_tokens if token not in stop_words]
        answer_core = [token for token in answer_tokens if token not in stop_words]

        exact_match = user_guess_norm and answer_norm and user_guess_norm == answer_norm
        substring_match = user_guess_norm and answer_norm and (
            user_guess_norm in answer_norm or answer_norm in user_guess_norm
        )
        core_match = bool(user_core) and bool(answer_core) and (
            user_core == answer_core or set(user_core) == set(answer_core)
        )

        if exact_match or substring_match or core_match:
            dispatcher.utter_message(text="Yes! That's correct.")
            return {
                "guess": value,
                "riddle_attempts": attempts,
                "riddle_trigger_text": None,
            }

        if attempts < 3:
            tries_left = 3 - attempts
            dispatcher.utter_message(
                text=f"No. Try again! ({tries_left} {'try' if tries_left == 1 else 'tries'} left)"
            )
            return {"guess": None, "riddle_attempts": attempts}

        dispatcher.utter_message(text=f"Nope — third try. The answer was: {answer}.")
        return {
            "guess": value,
            "riddle_attempts": attempts,
            "riddle_trigger_text": None,
        }


class ActionResetRiddle(Action):
    def name(self) -> Text:
        return "action_reset_riddle"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        return [
            SlotSet("riddle_question", None),
            SlotSet("riddle_answer", None),
            SlotSet("riddle_attempts", None),
            SlotSet("guess", None),
            SlotSet("riddle_trigger_text", None),
        ]
