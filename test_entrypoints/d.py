import argparse
from a import A  # Assuming class A is defined in module a
from b import B

class D:
    def __init__(self, user_input):
        self.user_input = user_input
        # Use class A, which internally uses classes B and C
        self.a = A()  
        self.b = B()

    def run(self):
        print("[D] Starting process with input:", self.user_input)
        self.b.handle_request(self.user_input)

def main():
    parser = argparse.ArgumentParser(
        description="Dummy entrypoint with branching for different CLI frameworks."
    )
    parser.add_argument("input", help="Input string to process.")
    parser.add_argument(
        "--entry", "-e", 
        default="D", 
    )
    args = parser.parse_args()

    if args.entry == "D":
        d = D(args.input)
        d.run()
    elif args.entry == "A":
        a = A()
        a.handle_request(args.input)
    else:
        print("Django entrypoint simulation. You might run 'django-admin' or manage.py commands here.")

if __name__ == "__main__":
    main()