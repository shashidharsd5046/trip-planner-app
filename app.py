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

def create_new_user(display_name, lat_str, lon_str, interests, notes):
    if not display_name:
        return "❌ Enter a display name", "", ""
    try:
        lat = float(lat_str) if lat_str else None
        lon = float(lon_str) if lon_str else None
        result = create_user(display_name, lat, lon, interests, notes)
        if result.get('success'):
            return f"✅ {result['message']}\n\n**User ID:** `{result['user_id']}`", result['user_id'], result['user_id']
        return f"❌ Error: {result.get('error')}", "", ""
    except Exception as e:
        return f"❌ Error: {str(e)}", "", ""

def create_new_trip(name, start, end, user_id, destination, lat_str, lon_str):
    global current_trip_id
    if not name or not start or not end or not user_id:
        return "❌ Fill all fields, including User ID"
    user_id = user_id.strip()
    try:
        result = create_trip(name, start, end, user_id)
        if result.get('success'):
            current_trip_id = result['trip_id']
            destination_message = ""
            if destination:
                lat = float(lat_str) if lat_str else None
                lon = float(lon_str) if lon_str else None
                if lat is None or lon is None:
                    return f"✅ Trip created but destination was not added.\n\n**Trip ID:** `{current_trip_id}`\n\n❌ Provide both destination coordinates."
                destination_id = ingest_destination_data(
                    current_trip_id, destination, 0, lat, lon
                )
                if destination_id:
                    destination_message = f"\n\n✅ Destination added: **{destination}**"
                else:
                    destination_message = "\n\n⚠️ Trip created, but destination ingestion failed."
            msg = f"✅ {result['message']}\n\n**Trip ID:** `{current_trip_id}`{destination_message}"
            return msg
        return f"❌ Error: {result.get('error')}"
    except Exception as e:
        return f"❌ Error: {str(e)}"


def add_dest(place, lat_str, lon_str):
    global current_trip_id
    
    # If no trip selected, get the most recent one from database
    if not current_trip_id:
        try:
            trips = get_all_trips()
            if not trips:
                return "❌ Create a trip first!"
            current_trip_id = trips[0]['trip_id']  # Most recent trip
        except:
            return "❌ Could not load trips from database"
    
    if not place:
        return "❌ Provide place name"
    try:
        lat = float(lat_str) if lat_str else None
        lon = float(lon_str) if lon_str else None
        if lat is None or lon is None:
            return "❌ Provide coordinates"
        destination_id = ingest_destination_data(current_trip_id, place, 0, lat, lon)
        return (
            f"✅ Added {place} to trip and seeded weather/activities"
            if destination_id
            else "❌ Destination ingestion failed"
        )
    except Exception as e:
        return f"❌ Error: {str(e)}"


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
                    gr.Markdown("### Create User")
                    user_name = gr.Textbox(label="Display Name", placeholder="Test User")
                    with gr.Row():
                        user_lat = gr.Textbox(label="Home Latitude", placeholder="37.7749")
                        user_lon = gr.Textbox(label="Home Longitude", placeholder="-122.4194")
                    user_interests = gr.Textbox(label="Interests", placeholder="hiking, museums, food")
                    user_notes = gr.Textbox(label="Planning Notes", placeholder="Travelling with children; avoid long walks")
                    user_btn = gr.Button("Create User", variant="secondary")
                    user_out = gr.Markdown()
                    user_id_for_trip = gr.Textbox(label="User ID", placeholder="Create a user first")

                    gr.Markdown("### Create Trip")
                    trip_name = gr.Textbox(label="Trip Name", placeholder="My Adventure")
                    with gr.Row():
                        start_date = gr.Textbox(label="Start", placeholder="YYYY-MM-DD")
                        end_date = gr.Textbox(label="End", placeholder="YYYY-MM-DD")
                    trip_destination = gr.Textbox(label="First Destination", placeholder="San Francisco")
                    with gr.Row():
                        trip_lat = gr.Textbox(label="Destination Latitude", placeholder="37.7749")
                        trip_lon = gr.Textbox(label="Destination Longitude", placeholder="-122.4194")
                    create_btn = gr.Button("Create", variant="primary")
                    create_out = gr.Markdown()
                with gr.Column():
                    gr.Markdown("### Add Destination")
                    gr.Markdown("*Destinations auto-add to your most recent trip*")
                    place_name = gr.Textbox(label="Place", placeholder="San Francisco")
                    with gr.Row():
                        lat = gr.Textbox(label="Latitude", placeholder="37.7749")
                        lon = gr.Textbox(label="Longitude", placeholder="-122.4194")
                    add_btn = gr.Button("Add")
                    add_out = gr.Markdown()
                    gr.Markdown("*Select a User ID below to query that user's trips.*")
                    query_user_id = gr.Textbox(label="User ID for Trip Query")
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

            user_btn.click(
                create_new_user,
                [user_name, user_lat, user_lon, user_interests, user_notes],
                [user_out, user_id_for_trip, query_user_id],
            )
            create_btn.click(
                create_new_trip,
                [trip_name, start_date, end_date, user_id_for_trip, trip_destination, trip_lat, trip_lon],
                create_out,
            )
            add_btn.click(add_dest, [place_name, lat, lon], add_out)
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
