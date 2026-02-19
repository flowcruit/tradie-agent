from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv
from agent import get_agent_response
import os

load_dotenv()

app = Flask(__name__)

twilio_client = Client(
    os.getenv("AC38de26539db94be86344ef15d8ca83ee"),
    os.getenv("d0558ee484d3ddca2d75db0432abe8e0")
)

@app.route("/sms", methods=["POST"])
def sms_reply():
    incoming_msg = request.form.get("Body", "")
    from_number = request.form.get("From", "")
    
    print(f"Message from {from_number}: {incoming_msg}")
    
    reply = get_agent_response(from_number, incoming_msg)
    
    print(f"Agent reply: {reply}")
    
    resp = MessagingResponse()
    resp.message(reply)
    return str(resp)

@app.route("/health", methods=["GET"])
def health():
    return "Tradie Agent is running!", 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
