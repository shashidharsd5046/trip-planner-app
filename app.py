"""Weather-Aware Trip Planning App"""

import gradio as gr
import gradio_client.utils as client_utils
import os

# --- Gradio/Pydantic schema-generation crash fix -----------------------
# Databricks' bundled Gradio 4.44 (in combination with newer Pydantic
# versions used by other libraries in this app) can produce a JSON schema
# where "additionalProperties" is a bare bool (True/False) instead of a
# dict. gradio_client.utils.get_type() assumes it's always a dict and does
# `if "const" in schema`, which throws:
#   TypeError: argument of type 'bool' is not iterable
#
# Patch the low-level function only. Do not replace Gradio routes or the
# Blocks instance methods: Databricks' frontend expects those responses.

_original_get_type = client_utils.get_type
_original_schema_to_python_type = client_utils._json_schema_to_python_type

def _safe_get_type(schema):
    if isinstance(schema, bool):
        return "Any"
    return _original_get_type(schema)

client_utils.get_type = _safe_get_type


def _safe_schema_to_python_type(schema, defs):
    # Gradio-client 1.3.0 reaches this function directly for JSON Schema
    # values such as additionalProperties=True.
    if isinstance(schema, bool):
        return "Any"
    return _original_schema_to_python_type(schema, defs)


client_utils._json_schema_to_python_type = _safe_schema_to_python_type
# -------------------------------------------------------------------------

# All files are in same folder now
from utils.database import *
from utils.agent_interface import send_message, reset_conversation
from data_ingestion import ingest_destination_data


def load_trips_list(user_id):
    if not user_id:
        return "Enter a User ID first."
    try:
        trips = get_trips_for_user(user_id)
        if not trips:
            return "No trips found."
        output = "## Your Trips\n\n"
        for t in trips:
            output += f"### {t['trip_name']}\n* **Dates:** {t['start_date']} to {t['end_date']}\n* **ID:** `{t['trip_id']}`\n\n"
        return output
    except Exception as e:
        return f"⚠️ Database connection issue: {str(e)}"


# Store current trip ID globally
current_trip_id = None

def create_user_trip(display_name, home_lat_str, home_lon_str, interests, notes,
                     trip_name, start, end, destination, lat_str, lon_str):
    """Create the complete user → trip → destination workflow once."""
    global current_trip_id
    required = [display_name, trip_name, start, end, destination, lat_str, lon_str]
    if not all(str(value).strip() for value in required):
        return "❌ Complete the user, trip, destination, latitude, and longitude fields."
    try:
        home_lat = float(home_lat_str) if home_lat_str else None
        home_lon = float(home_lon_str) if home_lon_str else None
        lat = float(lat_str)
        lon = float(lon_str)

        user_result = create_user(display_name, home_lat, home_lon, interests, notes)
        if not user_result.get('success'):
            return f"❌ User creation failed: {user_result.get('error')}"
        user_id = user_result['user_id']

        trip_result = create_trip(trip_name, start, end, user_id)
        if not trip_result.get('success'):
            return f"❌ Trip creation failed for user `{user_id}`: {trip_result.get('error')}"
        current_trip_id = trip_result['trip_id']

        destination_id = ingest_destination_data(current_trip_id, destination, 0, lat, lon)
        if not destination_id:
            return f"❌ Destination enrichment failed.\n\n**User ID:** `{user_id}`\n**Trip ID:** `{current_trip_id}`"

        return (
            "## ✅ Trip created successfully\n\n"
            f"**User ID:** `{user_id}`\n\n"
            f"**Trip ID:** `{current_trip_id}`\n\n"
            f"**Destination:** {destination}\n"
            "Weather, activities, and embeddings were populated before this success message."
        )
    except Exception as e:
        return f"❌ Workflow failed: {str(e)}"


def view_details(trip_id):
    if not trip_id:
        return "Enter trip ID"
    try:
        dests = get_trip_destinations(trip_id)
        if not dests:
            return f"No destinations for {trip_id}"
        output = f"## Destinations\n\n"
        for d in dests:
            output += f"* **{d['place_name']}** ({d['latitude']}, {d['longitude']})\n"
        return output
    except Exception as e:
        return f"❌ Error: {str(e)}"


def chat(msg, history):
    """Handle one chat turn using Gradio's schema-safe messages format."""
    if not msg or not msg.strip():
        return history or []

    # Gradio 4.44's legacy [[user, assistant], ...] Chatbot value can make
    # schema generation fail. Message dictionaries avoid that code path.
    history = list(history or [])
    history.append({"role": "user", "content": msg})
    try:
        response, tools = send_message(msg)
        formatted = response
        if tools:
            formatted += "\n\n---\n**🔧 Tools:**\n" + "\n".join([f"* {t['name']}" for t in tools])
        history.append({"role": "assistant", "content": formatted})
    except Exception as e:
        history.append({"role": "assistant", "content": f"❌ Error: {str(e)}"})
    return history


def clear_chat():
    reset_conversation()
    return []


def view_itin(trip_id):
    if not trip_id:
        return "Enter trip ID"
    try:
        items = get_trip_itinerary(trip_id)
        if not items:
            return f"No activities for {trip_id}"

        by_date = {}
        for item in items:
            date = item['scheduled_date']
            if date not in by_date:
                by_date[date] = []
            by_date[date].append(item)

        output = f"# Itinerary: {trip_id}\n\n"
        for date in sorted(by_date.keys()):
            output += f"## {date}\n\n"
            for item in by_date[date]:
                time = item['scheduled_start_time'] if item['scheduled_start_time'] else "All day"
                outdoor = "🌳" if item['outdoor'] else "🏠"
                output += f"### {outdoor} {item['activity_name']}\n* **Time:** {time}\n* **Location:** {item['place_name']}\n* **Duration:** {item['duration_minutes']} min\n"
                if item['reschedule_reason']:
                    output += f"\n⚠️ **Rescheduled:** {item['reschedule_reason']}\n"
                output += "\n"
            output += "---\n\n"
        return output
    except Exception as e:
        return f"❌ Error: {str(e)}"


def view_packing(trip_id):
    if not trip_id:
        return "Enter trip ID"
    try:
        items = get_packing_list(trip_id)
        if not items:
            return f"No packing items for {trip_id}"
        output = f"# 🎒 Packing List: {trip_id}\n\n"
        by_cat = {}
        for item in items:
            cat = item['category'] or 'Other'
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(item)
        for cat in sorted(by_cat.keys()):
            output += f"## {cat.title()}\n\n"
            for item in by_cat[cat]:
                check = "✅" if item['packed'] else "☐"
                pri = "🔴" if item['priority'] == 'high' else "🟡" if item['priority'] == 'medium' else "🟢"
                output += f"{check} {pri} **{item['item_name']}**\n"
                if item['reason']:
                    output += f"   *{item['reason']}*\n"
                output += "\n"
        return output
    except Exception as e:
        return f"❌ Error: {str(e)}"


with gr.Blocks(title="Weather Trip Planner", theme=gr.themes.Soft()) as app:
    gr.Markdown("# 🌤️ Weather-Aware Trip Planner")
    gr.Markdown("⚠️ **Note:** Database may take a moment to connect on first load.")

    with gr.Tabs():
        with gr.Tab("📅 Trip Management"):
            gr.Markdown("## Create & Manage Trips")
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Create User, Trip & Destination")
                    user_name = gr.Textbox(label="Display Name", placeholder="Test User")
                    with gr.Row():
                        user_lat = gr.Textbox(label="Home Latitude", placeholder="37.7749")
                        user_lon = gr.Textbox(label="Home Longitude", placeholder="-122.4194")
                    user_interests = gr.Textbox(label="Interests", placeholder="hiking, museums, food")
                    user_notes = gr.Textbox(label="Planning Notes", placeholder="Travelling with children; avoid long walks")
                    trip_name = gr.Textbox(label="Trip Name", placeholder="My Adventure")
                    with gr.Row():
                        start_date = gr.Textbox(label="Start", placeholder="YYYY-MM-DD")
                        end_date = gr.Textbox(label="End", placeholder="YYYY-MM-DD")
                    trip_destination = gr.Textbox(label="Destination", placeholder="London")
                    with gr.Row():
                        trip_lat = gr.Textbox(label="Destination Latitude", placeholder="37.7749")
                        trip_lon = gr.Textbox(label="Destination Longitude", placeholder="-122.4194")
                    create_btn = gr.Button("Create User + Trip", variant="primary")
                    create_out = gr.Markdown()
                    query_user_id = gr.Textbox(label="User ID for Trip Query", placeholder="Returned after creation")
            gr.Markdown("---")
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Your Trips")
                    load_btn = gr.Button("Load Trips")
                    trips_out = gr.Markdown()
                with gr.Column():
                    gr.Markdown("### Trip Details")
                    trip_id_view = gr.Textbox(label="Trip ID")
                    view_btn = gr.Button("View")
                    details_out = gr.Markdown()

            create_btn.click(
                create_user_trip,
                [user_name, user_lat, user_lon, user_interests, user_notes,
                 trip_name, start_date, end_date, trip_destination, trip_lat, trip_lon],
                [create_out],
            )
            load_btn.click(load_trips_list, inputs=query_user_id, outputs=trips_out)
            view_btn.click(view_details, trip_id_view, details_out)

        with gr.Tab("💬 Chat"):
            gr.Markdown("## AI Agent\n\nAsk: *Generate an itinerary for trip X*")
            # Use the message schema instead of the legacy nested tuple schema.
            # This avoids Gradio 4.44's JSON-schema validation crash.
            chatbot = gr.Chatbot(type="messages", height=400)
            with gr.Row():
                msg = gr.Textbox(label="Message", placeholder="Ask agent...", scale=4)
                send = gr.Button("Send", variant="primary", scale=1)
            clear = gr.Button("Clear")
            send.click(chat, [msg, chatbot], chatbot).then(lambda: "", outputs=msg)
            msg.submit(chat, [msg, chatbot], chatbot).then(lambda: "", outputs=msg)
            clear.click(clear_chat, outputs=chatbot)

        with gr.Tab("🗓️ Itinerary"):
            gr.Markdown("## View Itinerary & Packing")
            trip_id_itin = gr.Textbox(label="Trip ID")
            with gr.Row():
                view_itin_btn = gr.Button("View Itinerary", variant="primary")
                view_pack_btn = gr.Button("View Packing")
            itin_out = gr.Markdown(value="Enter trip ID above")
            view_itin_btn.click(view_itin, trip_id_itin, itin_out)
            view_pack_btn.click(view_packing, trip_id_itin, itin_out)

    gr.Markdown("---\n**Weather-Aware Trip Planning System** | Powered by Llama 3.3 70B")

if __name__ == "__main__":
    print("🚀 Starting Weather Trip Planner app...")
    # Databricks only needs the browser UI. Gradio 4.44 can crash while
    app.launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "8080")),
        share=False,
    )
