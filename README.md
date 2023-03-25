Document Summarizer
This is a web-based application that allows you to ingest multiple documents (such as docx, pdf, and txt files), extract summaries using the OpenAI GPT API, and compare and contrast the ideas in those documents using a chat interface.

Installation
Clone this repository to your local machine.
Create a new Python virtual environment for the project using python3 -m venv venv.
Activate the virtual environment using source venv/bin/activate.
Install the required Python packages using pip install -r requirements.txt.
Replace "YOUR_API_KEY" with your actual OpenAI API key in app.py.
Run the application using python app.py.
Usage
Navigate to http://localhost:5000 in your web browser.
Upload one or more documents using the file input form.
Click "Summarize" to extract summaries of the documents using the OpenAI GPT API.
Click "Compare & Contrast" to enter the chat interface and discuss the ideas presented in the documents.

Credits
This project was created by [YOUR NAME] as part of a [SCHOOL/COMPANY/PERSONAL] project. It uses the Flask web framework and the OpenAI GPT API for document summarization.
