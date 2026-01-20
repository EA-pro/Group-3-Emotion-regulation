from typing import Any, Dict, List, Text
import time

from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet, FollowupAction, Restarted
from rasa_sdk.executor import CollectingDispatcher


class ActionCheckSufficientFunds(Action):
    def name(self) -> Text:
        return "action_check_sufficient_funds"
    def run(self, dispatcher, tracker, domain):
        return []

class ActionStartReflectFlow(Action):
    def name(self) -> str:
        return "action_start_reflect_flow"

    def run(self, dispatcher, tracker, domain) -> List[Dict[str, Any]]:
        intent = tracker.latest_message.get("intent", {}).get("name")
        mood = tracker.get_slot("mood")
        support_completed = tracker.get_slot("support_completed")

        if isinstance(mood, str): mood_key = mood.lower()
        else: mood_key = None

        if not mood_key:
            intent_name = intent
            if intent_name == "mood_happy": mood_key = "happy"
            elif intent_name == "mood_sad": mood_key = "sad"
            elif intent_name == "mood_angry": mood_key = "angry"

        utter_mapping = {
            "happy": "utter_reflect_mood_happy",
            "sad": "utter_reflect_mood_sad",
            "angry": "utter_reflect_mood_angry",
        }

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
                return [SlotSet("support_completed", None), SlotSet("mood", mood_key), SlotSet("last_mood", mood_key)]

        utter_name = utter_mapping.get(mood_key)
        if utter_name:
            dispatcher.utter_message(response=utter_name)
            if mood_key in ["sad", "angry"]:
                dispatcher.utter_message(response="utter_reason_why_you_feel_upset_question")
            return [SlotSet("mood", mood_key), SlotSet("last_mood", mood_key)]

        dispatcher.utter_message(text="I didn't catch that — can you tell me how you feel?")
        return []

class ActionHandleReasonResponse(Action):
    def name(self) -> str:
        return "action_handle_reason_response"

    def run(self, dispatcher, tracker, domain) -> List[Dict[str, Any]]:
        intent = tracker.latest_message.get("intent", {}).get("name")
        if intent == "deny":
            dispatcher.utter_message(response="utter_acknowledge_uneasy_feeling")
            mood = tracker.get_slot("mood")
            if mood == "sad": dispatcher.utter_message(response="utter_overview_common_reasons_sad")
            elif mood == "angry": dispatcher.utter_message(response="utter_overview_common_reasons_angry")
            else: dispatcher.utter_message(response="utter_overview_common_reasons_sad")
            return [SlotSet("expect_free_reason", None)]
        elif intent == "affirm":
            dispatcher.utter_message(response="utter_ask_reason_after_affirm")
            return [SlotSet("expect_free_reason", True)]
        return []

class ActionHandlePickReason(Action):
    def name(self) -> str:
        return "action_handle_pick_reason"

    def run(self, dispatcher, tracker, domain) -> List[Dict[str, Any]]:
        intent = tracker.latest_message.get("intent", {}).get("name")
        expect_free_reason = tracker.get_slot("expect_free_reason")
        
        # 1. Extract Reason
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
        
        # 2. Handle "I don't know"
        if reason in ["dont_know", "I don't know", "I'm not sure"]:
             dispatcher.utter_message(response="utter_reason_unknown_exercise")
             dispatcher.utter_message(response="utter_reason_unknown_ask_later")
             return [SlotSet("reason", None), SlotSet("support_stage", None), SlotSet("expect_free_reason", None)]

        # 3. Traffic Control
        if reason:
            # Case A: Standard Button Click -> Start 4-Step Flow
            if intent == "pick_reason":
                dispatcher.utter_message(response="utter_divider")
                return [
                    SlotSet("reason", reason),
                    SlotSet("expect_free_reason", None),
                    SlotSet("support_stage", "common_ground"),
                    FollowupAction("action_trigger_search") # AI writes Stage 1
                ]
            
            # Case B: Serious/Complex Story (e.g., "I got pushed")
            # We skip the 4-step flow setup. We just send it to AI to reply naturally.
            # The intent here will likely be 'share_problem' or 'ask_question' or 'nlu_fallback'.
            else:
                return [
                    SlotSet("reason", reason),
                    SlotSet("expect_free_reason", None),
                    FollowupAction("action_trigger_search") # AI writes a direct reply
                ]

        return []

class ActionHandleSupportFlow(Action):
    def name(self) -> str:
        return "action_handle_support_flow"

    def run(self, dispatcher, tracker, domain) -> List[Dict[str, Any]]:
        intent = tracker.latest_message.get("intent", {}).get("name")
        stage = tracker.get_slot("support_stage")
        support_completed = tracker.get_slot("support_completed")

        # 1. If user asks a question during flow, pause and answer it.
        if intent in ["ask_question", "share_problem"]:
            return [SlotSet("support_stage", stage)]

        # 2. Mood check after completion
        if support_completed and intent in ("mood_happy", "mood_sad", "mood_angry"):
            mood_map = {"mood_happy": "happy", "mood_sad": "sad", "mood_angry": "angry"}
            next_mood = mood_map.get(intent)
            return [
                SlotSet("support_stage", None),
                SlotSet("reason", None),
                SlotSet("support_completed", None),
                SlotSet("mood", next_mood),
                SlotSet("last_mood", next_mood),
                FollowupAction("action_start_reflect_flow"),
            ]

        # 3. Calculate Next Stage
        next_stage = stage 
        if not stage:
            next_stage = "common_ground"
        elif intent == "affirm":
            if stage == "common_ground": next_stage = "acceptance"
            elif stage == "acceptance": next_stage = "analysis"
            elif stage == "analysis": next_stage = "nuance"
            elif stage == "nuance": 
                dispatcher.utter_message(response="utter_support_done_check_mood")
                return [SlotSet("support_stage", None), SlotSet("reason", None), SlotSet("support_completed", True)]
        elif intent == "deny":
            dispatcher.utter_message(text="No worries, we can pause here. I'm here if you need me.")
            return [SlotSet("support_stage", None), SlotSet("reason", None)]

        # 4. Trigger AI to speak the stage content
        return [
            SlotSet("support_stage", next_stage),
            FollowupAction("action_trigger_search")
        ]

class ActionGetStoredMood(Action):
    def name(self) -> str:
        return "action_get_stored_mood"
    def run(self, dispatcher, tracker, domain):
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
        dispatcher.utter_message(response="utter_restart_ok")
        return [Restarted(), FollowupAction("action_start_reflect_flow")]