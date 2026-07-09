provider "aws" {
    region = "us-east-1"
}

#create a aws security group block and two ingress block for 22 and 443
resource "aws_security_group" "drift-web_ssh_sg" {
  name        = "web_ssh_security_group"
  description = "Allow SSH and HTTPS inbound traffic"
}

#create ingress rule for 22
resource "aws_security_group_rule" "ssh_ingress" {
  type              = "ingress"
  from_port         = 22
  to_port           = 22
  protocol          = "tcp"
  security_group_id = aws_security_group.drift-web_ssh_sg.id
  cidr_blocks      = ["0.0.0.0/0"]
}

#create ingress rule for 443 port
resource "aws_security_group_rule" "https_ingress" {
  type              = "ingress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.drift-web_ssh_sg.id
  cidr_blocks      = ["0.0.0.0/0"]
}

resource "aws_instance" "drift-web_server" {
  ami           = "ami-0b6d9d3d33ba97d99" 
  instance_type = "t2.nano"
  vpc_security_group_ids = [aws_security_group.drift-web_ssh_sg.id]

  tags = {
    Name = "WebServer"
  }
}


