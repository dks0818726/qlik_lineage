import requests
 
qlik_server = "https://10.221.11.6/custom"
xrfkey = "1234567890abcdef"
 
url = f"{qlik_server}/qrs/stream/full?xrfkey={xrfkey}"
 
headers = {
    "X-Qlik-Xrfkey": xrfkey,
    "X-Qlik-User": "CORPORATE\\srv-qlik",
    "Content-Type": "application/json"
}
 
response = requests.get(
    url,
    headers=headers,
    verify=False
)
 
print(response.status_code)
print(response.text)