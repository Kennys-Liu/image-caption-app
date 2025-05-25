"""
COMP5349 Assignment: Image Captioning App using Gemini API and AWS Services

IMPORTANT:
Before running this application, ensure that you update the following configurations:
1. Replace the GEMINI API key (`GOOGLE_API_KEY`) with your own key from Google AI Studio.
2. Replace the AWS S3 bucket name (`S3_BUCKET`) with your own S3 bucket.
3. Update the RDS MySQL database credentials (`DB_HOST`, `DB_USER`, `DB_PASSWORD`).
4. Ensure all necessary dependencies are installed by running the provided setup script.

Failure to update these values will result in authentication errors or failure to access cloud services.
"""

# To use on an AWS Linux instance
# #!/bin/bash
# sudo yum install python3-pip -y
# pip install flask
# pip install mysql-connector-python
# pip install -q -U google-generativeai
# pip install boto3 werkzeug
# sudo yum install -y mariadb105

import boto3  # AWS S3 SDK
import mysql.connector  # MySQL database connector
from flask import Flask, request, render_template, jsonify  # Web framework
from werkzeug.utils import secure_filename  # Secure filename handling
import google.generativeai as genai  # Gemini API for image captioning
import base64  # Encoding image data for API processing
from io import BytesIO  # Handling in-memory file objects
import os # Added for path manipulation

# Configure Gemini API, REPLACE with your Gemini API key
GOOGLE_API_KEY = "AIzaSyA0qaBVwffTAdHBROB8oXpwlvH1URfQXmk"
genai.configure(api_key=GOOGLE_API_KEY)

# Choose a Gemini model for generating captions
model = genai.GenerativeModel(model_name="gemini-2.0-pro-exp-02-05")

def generate_image_caption(image_data):
    """
    Generate a caption for an uploaded image using the Gemini API.

    :param image_data: Raw binary image data
    :return: Generated caption or error message
    """
    try:
        encoded_image = base64.b64encode(image_data).decode("utf-8")
        response = model.generate_content(
            [
                {"mime_type": "image/jpeg", "data": encoded_image},
                "Caption this image.",
            ]
        )
        return response.text if response.text else "No caption generated."
    except Exception as e:
        return f"Error: {str(e)}"

# Flask app setup
app = Flask(__name__)

# AWS S3 Configuration, REPLACE with your S3 bucket
S3_BUCKET = "my-image-annotation-bucket"
S3_REGION = "us-east-1"


def get_s3_client():
    """Returns a new S3 client that automatically refreshes credentials if using an IAM role."""
    return boto3.client("s3", region_name=S3_REGION)

# Database Configuration, REPLACE with your RDS credentials
DB_HOST = "image-annotation-db.csp3b0ggwu41.us-east-1.rds.amazonaws.com"
# Changed DB_NAME to match your Lambda's image_metadata table context
DB_NAME = "image_caption_db" 
DB_USER = "admin"
DB_PASSWORD = "a1594579251"

def get_db_connection():
    """
    Establishes a connection to the MySQL RDS database.

    :return: Database connection object or None if connection fails
    """
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        return connection
    except mysql.connector.Error as err:
        print("Error connecting to database:", err)
        return None

# Allowed file types for upload
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

def allowed_file(filename):
    """
    Checks if the uploaded file has a valid extension.

    :param filename: Name of the uploaded file
    :return: True if valid, False otherwise
    """
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/")
def upload_form():
    """Render the homepage with the file upload form."""
    return render_template("index.html")

@app.route("/upload", methods=["GET", "POST"])
def upload_image():
    """
    Handles image upload, stores the file in AWS S3,
    generates a caption using Gemini API, and saves metadata in MySQL RDS.
    """
    if request.method == "POST":
        if "file" not in request.files:
            return render_template("upload.html", error="No file selected")

        file = request.files["file"]

        if file.filename == "":
            return render_template("upload.html", error="No file selected")

        if not allowed_file(file.filename):
            return render_template("upload.html", error="Invalid file type")

        filename = secure_filename(file.filename)
        file_data = file.read()  # Read file as binary

        # Upload file to S3 into the 'uploads/' prefix for Lambda triggers
        try:
            s3 = get_s3_client()  # Get a fresh S3 client
            # The key for S3 should include the 'uploads/' prefix
            s3_key = f"uploads/{filename}"
            s3.upload_fileobj(BytesIO(file_data), S3_BUCKET, s3_key)
            print(f"Uploaded original image to s3://{S3_BUCKET}/{s3_key}")
        except Exception as e:
            return render_template("upload.html", error=f"S3 Upload Error: {str(e)}")

        # Generate caption (this step is still done by the frontend for display,
        # but the Lambda will also generate and store it)
        caption = generate_image_caption(file_data)

        # Save metadata to the database, using 'image_metadata' table and 's3_key', 'description' fields
        try:
            connection = get_db_connection()
            if connection is None:
                return render_template("upload.html", error="Database Error: Unable to connect to the database.")
            cursor = connection.cursor()
            # Changed table and column names to match image_metadata
            cursor.execute(
                "INSERT INTO image_metadata (file_name, s3_key, description) VALUES (%s, %s, %s)",
                (filename, s3_key, caption), # file_name is just the filename, s3_key includes the prefix
            )
            connection.commit()
            connection.close()
            print(f"Saved metadata to database: file_name={filename}, s3_key={s3_key}, description='{caption}'")
        except Exception as e:
            return render_template("upload.html", error=f"Database Error: {str(e)}")

        # Prepare image for frontend display. For demonstration, show the original
        # The Lambda will generate a thumbnail in the 'thumbnails/' prefix.
        # For the gallery, we will fetch the thumbnail.
        encoded_image = base64.b64encode(file_data).decode("utf-8")
        file_url = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{s3_key}" # This will point to the original in 'uploads/'
        
        return render_template("upload.html", image_data=encoded_image, file_url=file_url, caption=caption)

    return render_template("upload.html")

@app.route("/gallery")
def gallery():
    """
    Retrieves images and their captions from the database,
    generates pre-signed URLs for secure access, and renders the gallery page.
    """
    try:
        connection = get_db_connection()
        if connection is None:
            return render_template("gallery.html", error="Database Error: Unable to connect to the database.")
        cursor = connection.cursor(dictionary=True)
        # Changed table and column names to match image_metadata
        # Changed order by to created_at to match your Lambda
        cursor.execute("SELECT s3_key, description FROM image_metadata ORDER BY created_at DESC")
        results = cursor.fetchall()
        connection.close()

        images_with_captions = []
        s3 = get_s3_client() # Get S3 client once for the loop

        for row in results:
            # Construct the thumbnail key
            # Assuming thumbnail key is 'thumbnails/' + original filename (without 'uploads/' prefix)
            original_filename = os.path.basename(row["s3_key"]) # Extracts 'image.jpg' from 'uploads/image.jpg'
            thumbnail_key = f"thumbnails/{original_filename}"
            
            # Generate pre-signed URL for the thumbnail
            try:
                # Check if thumbnail exists before generating URL
                s3.head_object(Bucket=S3_BUCKET, Key=thumbnail_key)
                thumbnail_url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": S3_BUCKET, "Key": thumbnail_key},
                    ExpiresIn=3600,  # URL expires in 1 hour
                )
                print(f"Generated presigned URL for thumbnail: {thumbnail_url}")
            except s3.exceptions.ClientError as e:
                if e.response['Error']['Code'] == '404':
                    print(f"Thumbnail not found for {row['s3_key']}. Using original image URL.")
                    # Fallback to original image if thumbnail is not ready or doesn't exist
                    thumbnail_url = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": S3_BUCKET, "Key": row['s3_key']},
                        ExpiresIn=3600,
                    )
                else:
                    print(f"Error checking thumbnail for {row['s3_key']}: {e}")
                    thumbnail_url = "#" # Fallback error URL

            images_with_captions.append(
                {
                    "url": thumbnail_url,
                    "caption": row["description"], # Changed to 'description'
                }
            )

        return render_template("gallery.html", images=images_with_captions)

    except Exception as e:
        print(f"Error in gallery route: {e}")
        return render_template("gallery.html", error=f"Database Error: {str(e)}")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)