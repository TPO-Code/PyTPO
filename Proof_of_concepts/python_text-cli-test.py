def main():
    while True:
        user_text = input("Enter something (or type 'quit' to exit): ")

        if user_text.lower() == "quit":
            print("Goodbye!")
            break

        print(f"You typed: {user_text}")


if __name__ == "__main__":
    main()