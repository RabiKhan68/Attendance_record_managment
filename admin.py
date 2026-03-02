from werkzeug.security import generate_password_hash

password = "admin123"  # choose any password you want
hashed_pw = generate_password_hash(password)
print(hashed_pw)