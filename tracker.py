from flask import Flask, redirect, request
import json
import os

app = Flask(__name__)
DATA_FILE = "agency_database.json"

@app.route('/click/<client_name>')
def track_click(client_name):
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
        
        if client_name in data:
            # Increment the click count
            data[client_name]["clicks"] = data[client_name].get("clicks", 0) + 1
            
            with open(DATA_FILE, "w") as f:
                json.dump(data, f)
            
            # Send them to the destination stored in your data
            return redirect(data[client_name].get("cta_link", "https://google.com"))
    
    return "Link Expired", 404

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
