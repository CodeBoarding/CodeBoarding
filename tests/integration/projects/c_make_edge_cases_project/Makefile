CC = gcc
CFLAGS = -Iinclude -std=c11

OBJS = src/main.o src/models/base.o src/models/entities.o src/services/processor.o

all: demo

demo: $(OBJS)
	$(CC) $(CFLAGS) -o demo $(OBJS)

src/main.o: src/main.c include/models/entities.h include/services/processor.h
	$(CC) $(CFLAGS) -c src/main.c -o src/main.o

src/models/base.o: src/models/base.c include/models/base.h
	$(CC) $(CFLAGS) -c src/models/base.c -o src/models/base.o

src/models/entities.o: src/models/entities.c include/models/entities.h include/models/base.h
	$(CC) $(CFLAGS) -c src/models/entities.c -o src/models/entities.o

src/services/processor.o: src/services/processor.c include/services/processor.h include/models/entities.h
	$(CC) $(CFLAGS) -c src/services/processor.c -o src/services/processor.o

clean:
	rm -f src/*.o src/models/*.o src/services/*.o demo
