class Addition:
    def operate(self, a, b):
        return a + b

class Subtraction:
    def operate(self, a, b):
        return a - b

class Multiplication:
    def operate(self, a, b):
        return a * b

class Division:
    def operate(self, a, b):
        if b == 0:
            raise ValueError("Cannot divide by zero.")
        return a / b

class Calculator:
    def __init__(self):
        self.operators = {
            '+': Addition(),
            '-': Subtraction(),
            '*': Multiplication(),
            '/': Division()
        }

    def parse_expression(self, expr):
        # Tokenize the expression
        tokens = expr.split()
        
        # Handle the case where tokens don't match the format
        if len(tokens) != 3:
            raise ValueError("Invalid expression format. Use 'num1 operator num2'.")

        num1, op, num2 = tokens

        # Convert numbers
        num1 = float(num1)
        num2 = float(num2)

        # Check if the operator is valid
        if op not in self.operators:
            raise ValueError(f"Invalid operator: {op}")

        # Get the result using the appropriate operator class
        result = self.operators[op].operate(num1, num2)
        result_2 = self.operators[op].operate(num1, num2)
        return result_2

    def run(self, expr):
        try:
            result = self.parse_expression(expr)
            print("Result:", result)
        except Exception as e:
            print("Error:", e)

if __name__ == "__main__":
    calculator = Calculator()
    calculator.run("5 + 10")