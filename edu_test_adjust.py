import openai
import streamlit as st
from dotenv import load_dotenv
import time, datetime
import re
import json
from os.path import basename
from openai import AssistantEventHandler
import os
import smtplib, ssl
from typing_extensions import override
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.utils import formataddr
from cryptography.fernet import Fernet
import firebase_admin
import base64
from firebase_admin import credentials, firestore



load_dotenv()

# Test Keys
# OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
# VECTOR_STORE_ID = os.getenv('VECTOR_STORE_ID')
# ASSISTANT_ID = os.getenv('ASSISTANT_ID')
# SERVICE_ACCOUNT= os.getenv('FIREBASE_SERVICE_ACCOUNT_KEY_BASE64')


# Hidden Keys

encryption_key = st.secrets['ENCRYPTION_KEY']
cipher_suite = Fernet(encryption_key.encode())


OPENAI_API_KEY_dec= st.secrets['OPENAI_API_KEY']
VECTOR_STORE_ID_dec= st.secrets['VECTOR_STORE_ID']
ASSISTANT_ID_dec= st.secrets['ASSISTANT_ID']
SERVICE_ACCOUNT_dec= st.secrets['SERVICE_ACCOUNT']


encrypted_secrets= {
    'OPENAI_API_KEY': OPENAI_API_KEY_dec,
    'VECTOR_STORE_ID': VECTOR_STORE_ID_dec,
    'ASSISTANT_ID': ASSISTANT_ID_dec,
    'SERVICE_ACCOUNT': SERVICE_ACCOUNT_dec
    }


decrypted_secrets = {}

for key, value in encrypted_secrets.items():
    print(f"Decrypting {key}: {value}")
    decrypted_secrets[key] = cipher_suite.decrypt(value.encode()).decode()


OPENAI_API_KEY= decrypted_secrets['OPENAI_API_KEY'] 
VECTOR_STORE_ID= decrypted_secrets['VECTOR_STORE_ID']
ASSISTANT_ID= decrypted_secrets['ASSISTANT_ID'] 
SERVICE_ACCOUNT= decrypted_secrets['SERVICE_ACCOUNT']



if not SERVICE_ACCOUNT:
    raise ValueError("Base64-encoded service account key not found in environment variables.")

try:
    # Decode the Base64 string
    decoded_key = base64.b64decode(SERVICE_ACCOUNT).decode('utf-8')

    # Parse the JSON string into a dictionary
    service_account_info = json.loads(decoded_key)
except Exception as e:
    raise ValueError(f"Failed to load service account key: {e}")


# Initialize Firebase Admin SDK
if not firebase_admin._apps:
    cred= credentials.Certificate(service_account_info)
    firebase_admin.initialize_app(cred)

db= firestore.client()

openai.api_key = OPENAI_API_KEY
client = openai.OpenAI(api_key=openai.api_key)
model = "gpt-4o"
assis_id = ASSISTANT_ID
vector_id = VECTOR_STORE_ID



# Functions

def get_all_threads():

    threads= db.collection('threads').stream()

    return [(thread.id, thread.to_dict().get('name','Untitled')) for thread in threads]


def get_thread_name(thread_id):
    doc = db.collection('threads').document(thread_id).get()
    if doc.exists:
        data = doc.to_dict()
        return data.get('name', 'Untitled')
    else:
        return 'Untitled'


def generate_thread_name(messages):

    global model

    # Concatenate user messages to form the conversation text
    conversation_text = "\n".join(
        [f"{msg['role'].capitalize()}: {msg['content']}" for msg in messages]
    )
    
    # Use OpenAI API to summarize the conversation
    response = client.chat.completions.create(
        model='gpt-4o-mini',  # Use an appropriate model
        messages= [
            {"role":"system", "content":"Summarize the main topic of the following user conversations in a short phrase"},
            {"role":"user", "content":f"{conversation_text}"}
            ],
        max_tokens=10,
        temperature=0.5,
        n=1,
        stop=None,
    )

    summary = response.choices[0].message.content
    print(summary)
    return summary if summary else "Untitled"


def update_thread_name(thread_id, thread_name):

    db.collection('threads').document(thread_id).update({'name': thread_name})


def save_thread(thread_id, messages, thread_name='Untitled'):

    try:
        thread_data= {
            'name': thread_name,
            'messages': messages,
        }

        # Saving thread messages to the database
        db.collection('threads').document(thread_id).set(thread_data)

    except Exception as e:
        print(f"Error saving thread {thread_id}: {e}")


def load_thread(thread_id):

    doc= db.collection('threads').document(thread_id).get()

    if doc.exists:
        data= doc.to_dict()

        return data.get('messages', [])
    else:
        return []


def create_new_thread():

    # Create a new thread using OpenAI API
    thread_create = client.beta.threads.create()
    thread_id_new = thread_create.id
    print(f"Created new thread: {thread_id_new}")

    # Initialize the conversation history for the new thread
    save_thread(thread_id_new, [], thread_name="Untitled")
    rename_untitled_threads()
    return thread_id_new


def rename_untitled_threads():

    # Query for docs named exactly "Untitled"
    untitled_docs = list(
        db.collection("threads").where("name", "==", "Untitled").stream()
    )
    
    if not untitled_docs:
        print("No untitled threads found.")
        return
    
    # Keep a dictionary of counters keyed by date:

    counters = {}
    
    for doc_snapshot in untitled_docs:
        doc_id = doc_snapshot.id
        
        # Creation timestamp:

        today_str = datetime.date.today().strftime("%Y-%m-%d")
        
        # Check if we already have a counter for today_str
        if today_str not in counters:
            counters[today_str] = 0
        
        # Increment the date-specific counter
        counters[today_str] += 1
        new_number = counters[today_str]

        # Build a new name
        new_name = f"Untitled_{today_str}_#{new_number}"
        
        # Update Firestore
        db.collection("threads").document(doc_id).update({"name": new_name})
    


# Functions

def write_file(file):

    with open(f'{file.name}', 'wb') as f:
        f.write(file.getbuffer())

    return f'{file.name}'


# Export text file

def remove_file(file_path):

    time.sleep(1)
    os.remove(file_path)


def download_file(file_data):

    # Display Content
    st.sidebar.code(file_data, language='html')

    response= "File made available for export in the sidebar. Please click the copy putton to access your data"

    return response


# End Run

def end_chat():
    st.session_state.start_chat= False
    st.session_state.thread_id= None
    st.session_state.messages= []
    st.rerun()


def delete_thread(thread_id):

    client.beta.threads.delete(
        thread_id=thread_id
    )

    db.collection('threads').document(thread_id).delete()

# Upload to OpenAI

def upload_openai(file_path, file_name):

    with open(file_path, "rb") as files:

        global thread_id

        message_file= client.files.create(
            file=files, purpose="assistants"
        )

        message= 'Upload the attached document to the thread database and wait for further instructions'

        upload_message= client.beta.threads.messages.create(
            thread_id= st.session_state.thread_id,
            role='user',
            content=message,
            attachments=[{"file_id":message_file.id,"tools":[{"type":"file_search"}]}]
        )

        # file_batch= client.beta.vector_stores.files.upload_and_poll(
        #         vector_store_id=vector_id, file= files
        #         )
                
        time.sleep(2)

        print(upload_message.status)
        st.sidebar.success(f'File successfully uploaded to thread: {file_name}')

    if os.path.exists(file_path):
        os.remove(file_path)


class EventHandler(AssistantEventHandler):
    @override
    def on_event(self, event):
        # Retrieve events that are denoted with 'requires_action'
        # since these will have our tool_calls
        if event.event == 'thread.run.requires_action':
            run_id = event.data.id
            self.handle_requires_action(event.data, run_id)


    def handle_requires_action(self, data, run_id):
        tool_outputs = []
        for tool in data.required_action.submit_tool_outputs.tool_calls:
            params_loaded = tool.function.arguments
            params = json.loads(params_loaded)
            print(f"Requested Tool: {tool.function.name}, Params: {params}")
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except json.JSONDecodeError as e:
                    print(f"Error decoding JSON: {e}")
            elif tool.function.name == "download_file":
                file_data = download_file(**params)
                tool_outputs.append({"tool_call_id": tool.id, "output": f'{file_data}'})
            # elif tool.function.name == "send_email":
            #     send_email_output = send_email(**params)
            #     tool_outputs.append({"tool_call_id": tool.id, "output": f'{send_email_output}'})

        self.submit_tool_outputs(tool_outputs, run_id)


    def submit_tool_outputs(self, tool_outputs, run_id):
        # Use the submit_tool_outputs_stream helper
        with client.beta.threads.runs.submit_tool_outputs_stream(
            thread_id=self.current_run.thread_id,
            run_id=run_id,
            tool_outputs=tool_outputs,
            event_handler=EventHandler(),
        ) as stream:
            stream.until_done()
            for text in stream.text_deltas:
                print(text, end="", flush=True)
            print()


# Start Run
def start_run(thread_id, assistant_id):
    try:
        with st.spinner('Typing...'):
            with client.beta.threads.runs.stream(
                thread_id=thread_id,
                assistant_id=assistant_id,
                event_handler=EventHandler()
            ) as stream:
                stream.until_done()
    except openai.BadRequestError as e:
        if 'already has an active run' in str(e):
            print("An active run is already in progress. Please wait for it to complete.")
        else:
            print(f"An error occurred: {e}")


def send_user_message(thread_id, content):
    user_message = client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=content
    )
    return user_message


def update_thread_name_after_message():

    if st.session_state.messages:
        thread_name= generate_thread_name(st.session_state.messages)
        update_thread_name(st.session_state.thread_id, thread_name)



# Fine-tune

def submit_fine_tune(thread_id, messages, thread_name):

    current_thread_name = get_thread_name(thread_name)

    try:

        thread_data= {
            'name': current_thread_name,
            'messages': messages,
        }


        # Saving thread messages to the database
        db.collection('fine-tuning').document(thread_id).set(thread_data)

        
    except Exception as e:
        print(f"Error saving thread {thread_id}: {e}")

    success_message= "Succesfully submitted for fine-tuning"

    return success_message


# Streamlit Application
st.set_page_config(page_title="Academic Assistant", page_icon=":robot_face:")
st.title("Academic Assistant")
st.write("Interact with the academic assistant for your needs")


# Initialize session state variables if they don't exist
if 'confirm_end_chat' not in st.session_state:
    st.session_state.confirm_end_chat = False

if 'end_chat_success' not in st.session_state:
    st.session_state.end_chat_success = False


# Initialize session state variables
if 'start_chat' not in st.session_state:
    st.session_state.start_chat = False


if 'thread_id' not in st.session_state:
    st.session_state.thread_id = None


if 'messages' not in st.session_state:
    st.session_state.messages = []


# Sidebar - Thread selection
all_threads = get_all_threads()

if all_threads:

    # Reverse the list to have the latest threads at the top
    all_threads= all_threads[::-1]


    # Prepare options and mapping
    thread_options = [name if name else thread_id for thread_id, name in all_threads]
    thread_id_map = {name if name else thread_id: thread_id for thread_id, name in all_threads}


    # Determine the default index based on the current thread_id
    if st.session_state.thread_id in all_threads:
        current_name = get_thread_name(st.session_state.thread_id)
        default_index= all_threads.index(st.session_state.thread_id)

    else:
        default_index= 0 # Default to the latest thread

    selected_thread_name= st.sidebar.selectbox(
        "Select a conversation",
        thread_options,
        index= default_index
    )


    selected_thread_id = thread_id_map[selected_thread_name]


    if selected_thread_id != st.session_state.thread_id:
        st.session_state.thread_id = selected_thread_id
        st.session_state.messages = load_thread(selected_thread_id)
else:
    st.sidebar.write("No conversations yet.")
    st.session_state.thread_id= None
    st.session_state.messages=[]


# Buttons to manage chat sessions
if st.sidebar.button("New Chat"):
    st.session_state.start_chat = True
    st.session_state.thread_id = create_new_thread()  # Assuming create_new_thread returns thread_id
    st.session_state.messages = []

    # Save the new thread to Firestore
    save_thread(st.session_state.thread_id, st.session_state.messages, thread_name="Untitled")
    st.rerun()


if st.sidebar.button("End Chat"):

    # Set the confirmation flag
    st.session_state.confirm_end_chat = True


# Check if we are in the confirmation state
if st.session_state.confirm_end_chat:

    choice= st.sidebar.radio("Are you sure you want to end and delete this chat?", ("Select a Choice","Yes", "No"))


    if choice == "Yes":
        # Perform the action to end and delete the chat

        if st.session_state.messages:
            thread_name= generate_thread_name(st.session_state.messages)
            update_thread_name(st.session_state.thread_id, thread_name)
        
        else:
            thread_name= "Untitled"

        delete_thread(st.session_state.thread_id)
        st.session_state.start_chat = False
        st.session_state.thread_id = None
        st.session_state.messages = []
        st.session_state.confirm_end_chat = False  # Reset the confirmation flag
        st.session_state.end_chat_success = True  # Set success message flag
        st.rerun()

        st.sidebar.success("Chat has been ended and deleted.")


    elif choice == "No":
        st.sidebar.info("Chat not deleted.")
        st.session_state.confirm_end_chat = False  # Reset the confirmation flag
        

# Display success message if needed
if st.session_state.end_chat_success:
    st.sidebar.success("Chat has been ended and deleted.")
    st.session_state.end_chat_success = False  # Reset success message flag


# Sidebar file uploader
file_uploader = st.sidebar.file_uploader(
    "Upload any file below",
    accept_multiple_files=True,
    key=0
)


if st.sidebar.button("Upload File"):
    if file_uploader is not None:
        for file in file_uploader:
            file_name = os.path.basename(file.name)
            file_path = os.path.join(os.getcwd(), file_name)
            print(file_path)
            with open(file_path, "wb") as f:
                f.write(file.getbuffer())
            upload_openai(file_path, file_name)

    else:
        st.warning("No files selected for upload.")


# Start Chat
if st.sidebar.button("Start Chat"):
    st.session_state.start_chat = True
    st.session_state.thread_id = selected_thread_id
    st.session_state.messages = load_thread(selected_thread_id)


# Mark for Fine-Tuning
if st.sidebar.button("Fine-Tune"):
    
    submit_fine_tune(thread_id=st.session_state.thread_id, messages=st.session_state.messages, thread_name=st.session_state.thread_id)

    st.sidebar.warning("Conversation submitted for fine-tuning")


# Main chat interface
if st.session_state.start_chat and st.session_state.thread_id is not None:

    # Display existing messages
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Chat input for the user
    if prompt := st.chat_input("Enter text here..."):

        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)

        # Add user's message to session state and database
        st.session_state.messages.append({"role": "user", "content": prompt})
        save_thread(st.session_state.thread_id, st.session_state.messages)

        # Send user's message to OpenAI API
        send_user_message(st.session_state.thread_id, prompt)
        start_run(st.session_state.thread_id, assis_id)

        # Retrieve assistant's response
        assis_messages = client.beta.threads.messages.list(
            thread_id=st.session_state.thread_id
        )
        # Find the latest assistant message
        assistant_message = None
        for msg in assis_messages.data:
            if msg.role == 'assistant':
                assistant_message = msg
                break
        if assistant_message:
            final_response = assistant_message.content[0].text.value
            final_clean = re.sub(r'【\d+:\d+†.*?】', '', final_response)

            # Display assistant's message
            with st.chat_message("assistant"):
                st.markdown(final_clean)

            # Add assistant's message to session state and database
            st.session_state.messages.append(
                {"role": "assistant", "content": final_clean}
            )


            # Update thread name based on the conversation
            update_thread_name_after_message()  # Function that updates the thread name

            # Save updated messages to Firestore
            save_thread(st.session_state.thread_id, st.session_state.messages, get_thread_name(st.session_state.thread_id))
else:
    st.write("Please start a chat to begin.")

