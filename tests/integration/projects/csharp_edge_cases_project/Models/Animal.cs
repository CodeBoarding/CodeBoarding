namespace EdgeCases.Models;

public abstract class Animal
{
    public string Name { get; }

    protected Animal(string name)
    {
        this.Name = name;
    }

    public abstract string Speak();

    public virtual string Describe()
    {
        return $"{this.Name}: {this.Speak()}";
    }
}

public class Dog : Animal
{
    public string Breed { get; }

    public Dog(string name, string breed) : base(name)
    {
        this.Breed = breed;
    }

    public override string Speak()
    {
        return "Woof";
    }
}

public class Cat : Animal
{
    public bool Indoor { get; }

    public Cat(string name, bool indoor) : base(name)
    {
        this.Indoor = indoor;
    }

    public override string Speak()
    {
        return "Meow";
    }
}
