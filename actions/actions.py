from typing import Any, Dict, List, Text
import time

from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet, FollowupAction
from rasa_sdk.executor import CollectingDispatcher


class ActionCheckSufficientFunds(Action):
    def name(self) -> Text:
        return "action_check_sufficient_funds"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
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
        # Determine current intent and handle mood selection
        intent = tracker.latest_message.get("intent", {}).get("name")
        mood = tracker.get_slot("mood")

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
                return []
        elif intent == "affirm":
            # User knows the reason: ask them to pick which reason matches
            dispatcher.utter_message(response="utter_ask_reason_after_affirm")
            mood = tracker.get_slot("mood")
            if mood == "sad":
                dispatcher.utter_message(response="utter_overview_common_reasons_sad")
            elif mood == "angry":
                dispatcher.utter_message(response="utter_overview_common_reasons_angry")
            else:
                dispatcher.utter_message(response="utter_overview_common_reasons_sad")
                return []
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
        intent = tracker.latest_message.get("intent", {}).get("name")
        reason = tracker.get_slot("reason") or tracker.latest_message.get("entities", [{}])[0].get("value")
        mood = tracker.get_slot("mood")
        print(f"[action_handle_pick_reason] intent={intent} reason={reason} mood={mood}")

        # If the user selected or wrote "I don't know" - stop support flow
        if reason == "dont_know":
            dispatcher.utter_message(response="utter_reason_unknown_exercise")
            dispatcher.utter_message(response="utter_reason_unknown_ask_later")
            # Do NOT proceed to the support flow automatically for 'dont_know'.
            # Do not clear the 'mood' slot so we retain the user's emotional state
            # for future commands. Only clear the reason and support_stage.
            return [SlotSet("reason", None), SlotSet("support_stage", None)]

        # Acknowledge other selected reasons - generic response for now
        # Otherwise, if the user picked (or typed) a reason, continue to support flow
        if reason:
            # remove chitchat 'thanks' message; instead send a small divider and
            # proceed to the support flow. The 'dont_know' case is handled above.
            dispatcher.utter_message(response="utter_divider")
            # After a user picks a reason, proceed to the supportive activities flow
            return [SlotSet("reason", reason), FollowupAction("action_handle_support_flow")]

        # Otherwise, do nothing
        return []


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
        intent = tracker.latest_message.get("intent", {}).get("name")
        stage = tracker.get_slot("support_stage")
        mood = tracker.get_slot("mood")
        reason = tracker.get_slot("reason")

        print(f"[action_handle_support_flow] intent={intent} stage={stage} mood={mood} reason={reason}")

        # If no stage set, start with common ground.
        if not stage:
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
                reason = tracker.get_slot("reason") or ""
                mood = tracker.get_slot("mood") or ""

                # Reason -> “hard times pass” message (edit freely)
                PASS_MESSAGES = {
                    "missing someone": "Missing someone can really ache. 💛 The feeling can come in waves, but it won’t stay this intense forever — it usually softens little by little.",
                    "I'm tired": "When you’re tired, feelings can feel extra heavy. 💛 Rest and a bit of care for your body often makes it easier again.",
                    "change_in_routine": "Changes can feel scary or unsettling. 💛 Your brain needs time to get used to new things — and that’s something that gets easier with time.",
                    "worry_school": "School worries can feel big. 💛 They can shrink when we break them into tiny steps and get support.",
                    "frustration": "Frustration can feel like pressure inside. 💛 That pressure can ease when we slow down and do one small thing at a time.",
                    "someone_bothered_me": "When someone upsets you, it can stay stuck in your body for a while. 💛 But feelings move — they don’t stay the same forever.",
                    "feeling_ignored": "Feeling ignored can hurt. 💛 That hurt can soften, especially when we find a small way to feel seen again.",
                    "overstimulation": "Too much noise or too many things can overwhelm you. 💛 When things get calmer, your body usually settles too.",
                } 

                # fall back if reason isn't found
                pass_text = PASS_MESSAGES.get(reason, "Big feelings can feel endless, but they do pass. 💛 They can get smaller little by little.")

                dispatcher.utter_message(text=pass_text)
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
                # Final step done
                dispatcher.utter_message(response="utter_support_done")
                # Keep 'mood' so we remember the user's current emotion for later.
                return [SlotSet("support_stage", None), SlotSet("reason", None)]
            elif intent == "deny":
                dispatcher.utter_message(text="That’s okay — if you want to keep exploring another time, I’ll be right here.")
                # Keep 'mood' so we remember the user's current emotion for later.
                return [SlotSet("support_stage", None), SlotSet("reason", None)]

        # If none of the above matched, do nothing
        return []


class ActionGetStoredMood(Action):
    def name(self) -> str:
        return "action_get_stored_mood"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        last_mood = tracker.get_slot("last_mood")
        if last_mood:
            dispatcher.utter_message(text=f"I have stored that you felt {last_mood}. If you'd like, we can explore that more.")
        else:
            dispatcher.utter_message(text="I don't have a record of how you were feeling yet. Would you like to tell me?")
        return []