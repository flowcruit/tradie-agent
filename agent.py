from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """You are a helpful assistant for {business_name}. 
You answer on behalf of {owner}, an emergency plumber in Australia.

Your job is to:
1. Greet the customer warmly in a friendly Australian tone
2. Find out what their plumbing problem is
3. Find out their address and best contact number
4. Let them know {owner} will call them back within 15 minutes

Keep messages short - this is SMS. Max 2-3 sentences per reply.
Never say you are an AI. You are the assistant for {business_name}.
Use casual Australian English. Be warm and helpful.

If they mention flooding, burst pipes, or no hot water - mark it as URGENT in your response by starting with [URGENT].""".format(
    business_name=os.getenv("BUSINESS_NAME"),
    owner=os.getenv("BUSINESS_OWNER")
)

conversation_history = {}

def get_agent_response(phone_number, customer_message):
    if phone_number not in conversation_history:
        conversation_history[phone_number] = []
    
    conversation_history[phone_number].append({
        "role": "user",
        "content": customer_message
    })
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT}
        ] + conversation_history[phone_number]
    )
    
    agent_reply = response.choices[0].message.content
    
    conversation_history[phone_number].append({
        "role": "assistant", 
        "content": agent_reply
    })
    
    return agent_reply
