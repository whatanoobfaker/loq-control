KERNELVERSION ?= $(shell uname -r)
KSRC := /lib/modules/$(KERNELVERSION)/build

obj-m += legion-laptop.o

all:
	$(MAKE) -C $(KSRC) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KSRC) M=$(CURDIR) clean
